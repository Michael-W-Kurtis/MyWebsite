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

# Deliberately separate from the wrapper's semaphore: a ten-minute batch must not
# lock the single-image page out, and a burst of wrapper traffic must not stall a
# batch that is already half done.
_batch_semaphore = asyncio.Semaphore(config.BATCH_CONCURRENCY)

# batch_id -> set of live subprocesses, so a cancel can actually kill the work
# rather than merely stop asking for more of it.
_live: dict[str, set] = {}
_cancelled: set[str] = set()

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF"}


class ImageRejected(ValueError):
    """The uploaded bytes are not an image we are willing to process."""


class SortFailed(RuntimeError):
    """pixelsort exited non-zero or ran past the timeout."""


class SortCancelled(RuntimeError):
    """The batch this job belonged to was cancelled."""


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
                        interval_file: Path | None = None,
                        *, batch_id: str | None = None) -> tuple[list[str], int]:
    """Execute pixelsort. Returns (argv_as_displayed, duration_ms).

    Passing batch_id routes the job through the batch semaphore and its longer
    timeout, and registers the process so it can be killed on cancel.
    """
    argv = pixelsort_spec.build_argv(
        params,
        input_path=str(input_path),
        output_path=str(output_path),
        interval_file=str(interval_file) if interval_file else None,
    )
    # Display uses "python"; execution uses this interpreter explicitly so the
    # venv is honoured regardless of PATH.
    exec_argv = [sys.executable] + argv[1:]

    semaphore = _batch_semaphore if batch_id else _semaphore
    timeout = config.BATCH_JOB_TIMEOUT if batch_id else config.JOB_TIMEOUT_SECONDS

    started = time.perf_counter()
    async with semaphore:
        if batch_id and batch_id in _cancelled:
            raise SortCancelled("This batch was cancelled.")
        proc = await asyncio.create_subprocess_exec(
            *exec_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if batch_id:
            _live.setdefault(batch_id, set()).add(proc)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SortFailed(
                f"The sort ran past {timeout}s and was stopped. "
                "Try a smaller image or a simpler interval function."
            ) from None
        finally:
            if batch_id:
                _live.get(batch_id, set()).discard(proc)

    duration_ms = int((time.perf_counter() - started) * 1000)

    if batch_id and batch_id in _cancelled:
        raise SortCancelled("This batch was cancelled.")

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
        elif token.startswith(str(config.BATCH_DIR)):
            # ./batches/<batch id>/source.png
            token = "./batches/" + "/".join(Path(token).parts[-2:])
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


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def normalize_full_size(raw: bytes, dest: Path) -> tuple[Path, int, int]:
    """Intake for the batch app: keep the input's own resolution.

    Differs from normalize_image() only in the cap it applies. The wrapper shrinks
    to MAX_IMAGE_DIM because it is a fast interactive loop; the batch keeps the
    original size and applies BATCH_MAX_EDGE purely as an accident guard.
    Returns (path, width, height).
    """
    import io

    if not raw:
        raise ImageRejected("The file was empty.")
    if len(raw) > config.MAX_UPLOAD_BYTES:
        limit_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ImageRejected(f"That file is larger than the {limit_mb} MB limit.")

    try:
        Image.open(io.BytesIO(raw)).verify()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageRejected("That file could not be read as an image.") from exc

    if image.format and image.format.upper() not in ALLOWED_FORMATS:
        raise ImageRejected(f"{image.format} files are not supported.")

    image = image.convert("RGB")

    cap = config.BATCH_MAX_EDGE
    if cap and max(image.size) > cap:
        scale = cap / max(image.size)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
        log.info("Batch source capped to %sx%s (BATCH_MAX_EDGE=%s)", *image.size, cap)

    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="PNG", optimize=False)
    return dest, image.width, image.height


def write_view_derivative(full: Path) -> Path:
    """Downscale a finished render for display in the grid.

    This is not a shortcut on quality. The PNG beside it is the artifact, sorted at
    full resolution; this is the same picture viewed smaller, exactly as any image
    viewer would show it. Without it the browser holds 16 full-size decoded bitmaps
    and the tab dies.
    """
    view = full.with_suffix(".view.webp")
    if view.exists() and view.stat().st_mtime >= full.stat().st_mtime:
        return view
    with Image.open(full) as im:
        im = im.convert("RGB")
        edge = config.BATCH_VIEW_EDGE
        if max(im.size) > edge:
            scale = edge / max(im.size)
            im = im.resize(
                (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                Image.LANCZOS,
            )
        im.save(view, format="WEBP", quality=80, method=4)
    return view


def cancel_batch(batch_id: str) -> int:
    """Kill every in-flight process for a batch. Returns how many were killed."""
    _cancelled.add(batch_id)
    killed = 0
    for proc in list(_live.get(batch_id, set())):
        try:
            proc.kill()
            killed += 1
        except ProcessLookupError:
            pass
    _live.pop(batch_id, None)
    log.info("Cancelled batch %s, killed %d process(es)", batch_id, killed)
    return killed


def is_cancelled(batch_id: str) -> bool:
    return batch_id in _cancelled


def clear_cancel(batch_id: str) -> None:
    _cancelled.discard(batch_id)


def sweep_batches() -> int:
    """Remove batch working directories past BATCH_RETENTION_HOURS. 0 keeps forever."""
    hours = config.BATCH_RETENTION_HOURS
    if hours <= 0:
        return 0
    import shutil

    cutoff = time.time() - hours * 3600
    removed = 0
    for directory in config.BATCH_DIR.glob("*"):
        if not directory.is_dir():
            continue
        try:
            if directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        except OSError:
            log.warning("Could not remove %s", directory)
    if removed:
        log.info("Swept %d expired batch dir(s)", removed)
    return removed
