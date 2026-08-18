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

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")

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
        manifest = _find(build, "*.json")
        if manifest is None:
            return {"error": "Found UnityLoader.js but no .json manifest beside it."}
        return {
            "generation": "legacy",
            "loader": loader.name,
            "manifest": manifest.name,
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


def _read_about(directory: Path, slug: str) -> dict:
    about = {
        "title": slug.replace("-", " ").title(),
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


def load_game(slug: str) -> dict | None:
    """Return one game's descriptor, or None if the slug is not a real game."""
    if not SLUG.match(slug or ""):
        return None
    directory = config.GAMES_DIR / slug
    if not directory.is_dir():
        return None

    about = _read_about(directory, slug)
    build = _inspect_build(directory / "build")

    game = {
        "slug": slug,
        "href": f"/games/{slug}",
        "build_url": f"/games/{slug}/build",
        "playable": bool(build) and "error" not in build,
        "problem": (build or {}).get("error") or ("No build/ directory." if build is None else None),
        "has_cover": (directory / "cover.png").exists(),
        **about,
    }
    if game["playable"]:
        game["build"] = build
    return game


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
