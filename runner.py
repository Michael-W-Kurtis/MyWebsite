"""Image intake and pixelsort execution.

Two deliberate choices worth stating plainly:

* pixelsort is invoked as a subprocess (`python -m pixelsort ...`) rather than
  imported as a library. Importing would be marginally faster, but then the
  command string shown above the SORT button would be a description of what
  happened rather than the thing that ran. The subprocess also gives a hard kill
  on a runaway job, which an in-process call does not.

* Every upload is decoded, downscaled and re-encoded as PNG before it is touched
  again. That single step neutralises EXIF payloads, polyglot files and mislabelled
  content types, and it caps the cost of the sort.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import config, pixelsort_spec

log = logging.getLogger("site.runner")

Image.MAX_IMAGE_PIXELS = config.MAX_DECODE_PIXELS

_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF"}


class ImageRejected(ValueError):
    """The uploaded bytes are not an image we are willing to process."""


class SortFailed(RuntimeError):
    """pixelsort exited non-zero or ran past the timeout."""


def normalize_image(raw: bytes, dest_dir: Path, stem: str | None = None) -> Path:
    """Validate, downscale and re-encode to PNG. Returns the saved path."""
    if not raw:
        raise ImageRejected("The file was empty.")
    if len(raw) > config.MAX_UPLOAD_BYTES:
        limit_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ImageRejected(f"That file is larger than the {limit_mb} MB limit.")

    import io

    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()  # structural check; consumes the file object
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageRejected("That file could not be read as an image.") from exc

    if image.format and image.format.upper() not in ALLOWED_FORMATS:
        raise ImageRejected(f"{image.format} files are not supported.")

    image = image.convert("RGB")

    longest = max(image.size)
    if longest > config.MAX_IMAGE_DIM:
        scale = config.MAX_IMAGE_DIM / longest
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.LANCZOS)
        log.info("Downscaled upload to %sx%s", *new_size)

    name = f"{stem or uuid.uuid4().hex}.png"
    dest = dest_dir / name
    # Re-encoding from the decoded pixel buffer drops all original metadata.
    image.save(dest, format="PNG", optimize=True)
    return dest


def result_path_for(params: dict, input_path: Path, prefix: str) -> Path:
    """Deterministic output name, so repeat requests are a cache hit."""
    try:
        fingerprint = f"{input_path.name}:{input_path.stat().st_mtime_ns}"
    except OSError:
        fingerprint = input_path.name
    key = fingerprint + "|" + repr(sorted(params.items()))
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return config.RESULT_DIR / f"{prefix}-{digest}.png"


async def run_pixelsort(params: dict, input_path: Path, output_path: Path,
                        interval_file: Path | None = None) -> tuple[list[str], int]:
    """Execute pixelsort. Returns (argv_as_displayed, duration_ms)."""
    argv = pixelsort_spec.build_argv(
        params,
        input_path=str(input_path),
        output_path=str(output_path),
        interval_file=str(interval_file) if interval_file else None,
    )
    # Display uses "python"; execution uses this interpreter explicitly so the
    # venv is honoured regardless of PATH.
    exec_argv = [sys.executable] + argv[1:]

    started = time.perf_counter()
    async with _semaphore:
        proc = await asyncio.create_subprocess_exec(
            *exec_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=config.JOB_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SortFailed(
                f"The sort ran past {config.JOB_TIMEOUT_SECONDS}s and was stopped. "
                "Try a smaller image or a simpler interval function."
            ) from None

    duration_ms = int((time.perf_counter() - started) * 1000)

    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        message = detail[-1] if detail else f"exit code {proc.returncode}"
        raise SortFailed(f"pixelsort failed: {message}")

    if not output_path.exists():
        raise SortFailed("pixelsort reported success but wrote no output file.")

    return argv, duration_ms


def display_command(argv: list[str]) -> str:
    """Render argv for the on-page command readout using site-relative paths."""
    parts = []
    for token in argv:
        if token.startswith(str(config.UPLOAD_DIR)):
            token = "./UploadedImages/" + Path(token).name
        elif token.startswith(str(config.RESULT_DIR)):
            token = "./results/" + Path(token).name
        elif "/static/img/" in token:
            token = "./" + Path(token).name
        parts.append(token)
    return " ".join(parts)


def sweep_old_files() -> int:
    """Delete uploads and results older than RETENTION_HOURS. Returns count removed."""
    if config.RETENTION_HOURS <= 0:
        return 0
    cutoff = time.time() - config.RETENTION_HOURS * 3600
    removed = 0
    for directory in (config.UPLOAD_DIR, config.RESULT_DIR):
        for path in directory.glob("*"):
            if not path.is_file():
                continue
            # Portrait renders are cheap to keep and expensive to recompute.
            if path.name.startswith("portrait-"):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                log.warning("Could not remove %s", path)
    if removed:
        log.info("Swept %d expired file(s)", removed)
    return removed
