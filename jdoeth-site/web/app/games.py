"""
Unity WebGL game registry.

Scans GAMES_DIR for playable builds and works out how to serve each one. Two
things here are not cosmetic — get either wrong and the game shows a progress bar
that never finishes:

1. **Content-Encoding.** Unity's `.unityweb` files (and modern `.br` / `.gz`
   files) are *pre-compressed on disk*. The browser only unpacks them if the
   server declares the encoding. Miss the header and Unity's loader either fails
   outright or falls back to a slow JS decompressor with a console warning about
   the web server being misconfigured. This is far and away the most common Unity
   WebGL hosting failure, so encoding is detected per file from its actual bytes
   rather than assumed from its name.

   gzip has magic bytes (1f 8b). Brotli has none, so it is inferred: a
   `.unityweb` file that is neither gzip nor a recognisable uncompressed Unity
   payload is treated as brotli. An explicit `"compression"` key in about.json
   overrides the guess.

2. **Loader generation.** Unity changed its WebGL API in 2020. Builds up to
   2019.x ship `UnityLoader.js` and are started with `UnityLoader.instantiate()`.
   2020+ ships `<name>.loader.js` and uses `createUnityInstance()`. The two are
   not interchangeable, so the generation is detected and the page branches on it.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import config

log = logging.getLogger("site.games")

# Folder names become URLs. Uppercase and underscores are allowed because people
# name folders after their game ("MonkeyKongsBGG"), and silently ignoring such a
# folder is a far worse outcome than a mixed-case URL. Dots and slashes stay out,
# which is what keeps resolve_asset() safe from traversal.
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,48}$")

# Extensions that may sit in front of a compression suffix.
_MIME = {
    ".js": "text/javascript",
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".data": "application/octet-stream",
    ".mem": "application/octet-stream",
    ".symbols": "application/octet-stream",
    ".unityweb": "application/octet-stream",
    ".html": "text/html",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}

_COMPRESSION_SUFFIX = {".br": "br", ".gz": "gzip", ".gzip": "gzip"}

GZIP_MAGIC = b"\x1f\x8b"
WASM_MAGIC = b"\x00asm"
UNITY_DATA_MAGIC = b"UnityWebData"


def detect_encoding(path: Path) -> str | None:
    """Return the Content-Encoding this file needs, or None if it is plain."""
    suffix = _COMPRESSION_SUFFIX.get(path.suffix.lower())
    if suffix:
        # Modern builds name the encoding outright: game.wasm.br, game.data.gz.
        return suffix

    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return None

    if head.startswith(GZIP_MAGIC):
        return "gzip"
    if head.startswith(WASM_MAGIC) or head.startswith(UNITY_DATA_MAGIC):
        return None
    # A .unityweb that is plain text is an already-decompressed framework file.
    # Browsers hand you the decoded bytes when you "Save as" from the network
    # panel, so a build recovered that way is a mix of compressed and plain
    # files. Serving one of these as brotli would break it.
    if head and all(32 <= b < 127 or b in (9, 10, 13) for b in head):
        return None
    if path.suffix.lower() == ".unityweb":
        # Not gzip, not a bare payload. Brotli carries no signature, and an
        # uncompressed .unityweb is not a thing Unity produces.
        return "br"
    return None


def content_type(path: Path) -> str:
    """MIME type, looking through any compression suffix.

    `game.wasm.br` must be served as application/wasm, not as a brotli blob, or
    the browser will not use streaming compilation.
    """
    name = path.name.lower()
    for suffix in _COMPRESSION_SUFFIX:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return _MIME.get(Path(name).suffix, "application/octet-stream")


# Unity names every payload <stem><role>, where the stem is the build folder's
# name and the role is fixed. Matching on role lets a renamed file be recognised
# as the right file under the wrong name, which is a completely different problem
# from the file not being there at all.
ROLE_SUFFIXES = (
    ".wasm.code.unityweb",
    ".wasm.framework.unityweb",
    ".asm.code.unityweb",
    ".asm.framework.unityweb",
    ".asm.memory.unityweb",
    ".data.unityweb",
    ".mem.unityweb",
    ".data",
    ".wasm",
    ".mem",
)


def _role(name: str) -> str | None:
    lower = name.lower()
    for suffix in ROLE_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return None


def _manifest_refs(manifest: Path) -> tuple[list[tuple[str, str]], str | None]:
    """Filenames a legacy .json manifest points at, as (key, filename) pairs."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"{manifest.name} is not valid JSON ({exc})."
    if not isinstance(data, dict):
        return [], f"{manifest.name} is not a Unity manifest."
    refs = [
        (key, value)
        for key, value in data.items()
        if key.endswith("Url") and isinstance(value, str) and value.strip()
    ]
    return refs, None


def _pick_manifest(build: Path) -> Path | None:
    """The .json that is actually a Unity manifest.

    Taking the alphabetically-first .json breaks as soon as a build folder has a
    second one beside it, so the manifest is identified by having *Url keys.
    """
    candidates = sorted(build.glob("*.json"))
    for candidate in candidates:
        refs, _ = _manifest_refs(candidate)
        if refs:
            return candidate
    return candidates[0] if candidates else None


def _audit_payloads(build: Path, manifest: Path) -> dict:
    """Check that every file the manifest names is really on disk.

    This exists because of one specific, miserable failure mode. When a payload
    is missing, Unity fetches the 404, hands the response body to the JS engine
    as though it were the framework, and the browser reports a SyntaxError
    pointing at a blob URL. Nothing in that message mentions a missing file.
    Checking up front turns it into a sentence naming the file.
    """
    refs, error = _manifest_refs(manifest)
    if error:
        return {"error": error}

    on_disk = {p.name: p for p in build.iterdir() if p.is_file()}
    lowered = {name.lower(): name for name in on_disk}

    by_role: dict[str, list[str]] = {}
    for name in on_disk:
        role = _role(name)
        if role:
            by_role.setdefault(role, []).append(name)

    missing, empty, hints, absent, renames = [], [], [], [], []
    for _key, ref in refs:
        name = ref.split("/")[-1]
        if name in on_disk:
            if on_disk[name].stat().st_size == 0:
                empty.append(name)
            continue
        missing.append(name)
        # Case is the usual culprit when a build has moved between a Windows
        # machine and a Linux host, or been pulled down by a scraper.
        near = lowered.get(name.lower())
        if near:
            hints.append(
                f"'{name}' is missing, but '{near}' is present - the names differ "
                "only by capitalisation. Rename the file on disk to match the "
                "manifest exactly; this host is case-sensitive."
            )
            continue

        # Same role, different stem: the file is here under the wrong name.
        role = _role(name)
        candidates = [c for c in by_role.get(role, []) if c not in
                      {r["to"] for r in renames}] if role else []
        if len(candidates) == 1:
            renames.append({"from": candidates[0], "to": name})
        else:
            absent.append(name)

    if renames:
        want = renames[0]["to"][: -len(_role(renames[0]["to"]))]
        got = renames[0]["from"][: -len(_role(renames[0]["from"]))]
        hints.append(
            f"Unity built this as '{want}' and records that name inside the "
            f"manifest, but the files on disk say '{got}'. Unity build files "
            "should never be renamed - the manifest is the index."
        )

    total = sum(p.stat().st_size for p in on_disk.values())

    return {
        "manifest_stem": (renames[0]["to"][: -len(_role(renames[0]["to"]))]
                          if renames else None),
        "disk_stem": (renames[0]["from"][: -len(_role(renames[0]["from"]))]
                      if renames else None),
        "expected": [ref.split("/")[-1] for _key, ref in refs],
        "present": sorted(on_disk),
        "missing": missing,
        "absent": absent,
        "renames": renames,
        "empty": empty,
        "hints": hints,
        "total_bytes": total,
    }


def _find(build: Path, *patterns: str) -> Path | None:
    for pattern in patterns:
        matches = sorted(build.glob(pattern))
        if matches:
            return matches[0]
    return None


def _inspect_build(build: Path) -> dict | None:
    """Work out which Unity generation this build is and where its parts are."""
    if not build.is_dir():
        return None

    # --- Legacy: Unity 5.6 through 2019.x -------------------------------
    loader = _find(build, "UnityLoader.js")
    if loader:
        # The .json manifest names the real payload files, whatever they are
        # called, so it is the only path the page needs.
        manifest = _pick_manifest(build)
        if manifest is None:
            return {"error": "Found UnityLoader.js but no .json manifest beside it."}

        audit = _audit_payloads(build, manifest)
        if audit.get("error"):
            return {"error": audit["error"], "audit": audit}
        if audit["missing"] or audit["empty"]:
            parts = []
            if audit["renames"]:
                parts.append(f"{len(audit['renames'])} renamed")
            if audit["absent"]:
                parts.append(f"{len(audit['absent'])} missing entirely")
            if audit["empty"]:
                parts.append(f"{len(audit['empty'])} empty")
            return {
                "error": (f"{manifest.name} names files that build/ does not "
                          f"provide: {', '.join(parts)}."),
                "audit": audit,
                "manifest": manifest.name,
            }
        return {
            "generation": "legacy",
            "loader": loader.name,
            "manifest": manifest.name,
            "audit": audit,
        }

    # --- Modern: Unity 2020+ ---------------------------------------------
    loader = _find(build, "*.loader.js")
    if loader:
        stem = loader.name[: -len(".loader.js")]

        def part(*names):
            found = _find(build, *names)
            return found.name if found else None

        data = part(f"{stem}.data", f"{stem}.data.*")
        framework = part(f"{stem}.framework.js", f"{stem}.framework.js.*")
        code = part(f"{stem}.wasm", f"{stem}.wasm.*")
        missing = [n for n, v in (("data", data), ("framework", framework), ("wasm", code)) if not v]
        if missing:
            return {"error": f"Modern build is missing: {', '.join(missing)}."}
        return {
            "generation": "modern",
            "loader": loader.name,
            "data": data,
            "framework": framework,
            "code": code,
            "stem": stem,
        }

    return {"error": "No UnityLoader.js and no *.loader.js in build/."}


def _default_title(slug: str) -> str:
    """A readable title from a folder name.

    Only title-case names that are already lowercase; "MonkeyKongsBGG".title()
    would give "Monkeykongsbgg", which is worse than leaving it alone.
    """
    if slug.lower() != slug:
        return slug.replace("_", " ").replace("-", " ")
    return slug.replace("_", " ").replace("-", " ").title()


def _read_about(directory: Path, slug: str) -> dict:
    about = {
        "title": _default_title(slug),
        "tagline": "",
        "description": "",
        "year": None,
        "controls": [],
        "tags": ["Unity", "WebGL"],
        "width": 960,
        "height": 600,
        "background": "#0d1524",
        "compression": None,
    }
    path = directory / "about.json"
    if path.exists():
        try:
            about.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring malformed %s: %s", path, exc)

    # Templates address this as `row.input`. A dict key called "keys" would
    # resolve to dict.keys() in Jinja instead of the value, so it is normalised
    # here and both spellings are accepted in about.json.
    normalised = []
    for row in about.get("controls") or []:
        if isinstance(row, dict):
            normalised.append({
                "input": row.get("input") or row.get("keys") or "",
                "action": row.get("action", ""),
            })
    about["controls"] = normalised
    return about


def _locate_build(directory: Path) -> tuple[Path | None, bool]:
    """Find the Unity output. Returns (path, is_nested).

    The documented layout is <slug>/build/, matching Unity's own output folder
    and keeping about.json and cover.png clear of the build. But dropping the
    files straight into <slug>/ is an obvious thing to do, and rejecting that
    produces a "No build/ directory" message that reads like a bug. So both are
    accepted, and the docs still recommend the nested form.
    """
    nested = directory / "build"
    if nested.is_dir() and (
        (nested / "UnityLoader.js").exists() or any(nested.glob("*.loader.js"))
    ):
        return nested, True
    if (directory / "UnityLoader.js").exists() or any(directory.glob("*.loader.js")):
        return directory, False
    if nested.is_dir():
        return nested, True          # exists but empty/wrong - let the audit explain
    return None, True


def load_game(slug: str) -> dict | None:
    """Return one game's descriptor, or None if the slug is not a real game."""
    if not SLUG.match(slug or ""):
        return None
    directory = config.GAMES_DIR / slug
    if not directory.is_dir():
        return None

    about = _read_about(directory, slug)
    build_dir, nested = _locate_build(directory)
    build = _inspect_build(build_dir) if build_dir else None

    game = {
        "slug": slug,
        "href": f"/games/{slug}",
        # Flat layouts are served through /assets, which maps to the game folder
        # root; nested ones through /build.
        "build_url": f"/games/{slug}/build" if nested else f"/games/{slug}/assets",
        "playable": bool(build) and "error" not in build,
        "problem": (build or {}).get("error") or (
            "No Unity build found. Put the files in "
            f"web/content/games/{slug}/build/." if build is None else None),
        "layout": "nested" if nested else "flat",
        "has_cover": (directory / "cover.png").exists(),
        **about,
    }
    if game["playable"]:
        game["build"] = build
    if build and build.get("audit"):
        game["audit"] = build["audit"]

    # Naming the game folder "build" means the URL becomes /games/build and the
    # real Unity output ends up at games/build/build. It works, but it is
    # almost always a mis-step worth pointing out.
    if slug.lower() == "build":
        game["naming_warning"] = (
            "This game's folder is named 'build', so its address is /games/build. "
            "The folder directly under games/ is the game's name and becomes the "
            "URL; the Unity output goes in a 'build' folder inside it. Rename "
            "games/build/ to games/<the-game-name>/ and the address becomes "
            "/games/<the-game-name>/."
        )
    return game


def skipped_directories() -> list[dict]:
    """Folders under games/ that could not be used, and why.

    A directory that is quietly absent from the listing is the worst possible
    failure: the files are there, the page shows nothing, and there is nothing
    to search for. Anything rejected gets named on the games index instead.
    """
    if not config.GAMES_DIR.is_dir():
        return []
    out = []
    for directory in sorted(config.GAMES_DIR.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "_")):
            continue
        name = directory.name
        if SLUG.match(name):
            continue
        suggestion = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:49] or "game"
        out.append({
            "name": name,
            "reason": "The folder name contains characters that cannot appear in a "
                      "URL (letters, digits, hyphen and underscore only).",
            "suggestion": suggestion,
        })
    return out


def list_games() -> list[dict]:
    """Every game directory, playable or not, so misconfigured ones stay visible."""
    if not config.GAMES_DIR.is_dir():
        return []
    games = []
    for directory in sorted(config.GAMES_DIR.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "_")):
            continue
        game = load_game(directory.name)
        if game:
            games.append(game)
    # Playable first, so a half-copied build never sits above a working one.
    games.sort(key=lambda g: (not g["playable"], g["title"].lower()))
    return games


def resolve_asset(slug: str, relative: str) -> Path | None:
    """Map a request path to a file on disk, refusing anything outside the game.

    Everything under a game directory is fair game except its metadata, so Unity
    template folders (TemplateData, StreamingAssets) work untouched.
    """
    if not SLUG.match(slug or ""):
        return None
    root = (config.GAMES_DIR / slug).resolve()
    if not root.is_dir():
        return None
    try:
        target = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    if not target.is_file():
        return None
    if root not in target.parents:
        return None
    if target.name == "about.json":
        return None
    return target
