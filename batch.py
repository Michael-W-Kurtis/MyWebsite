"""Batch pixelsorter API.

Deliberately not a job queue. `runner.result_path_for()` names every output by a
hash of its parameters plus its input, so a finished cell already sits on disk under
a deterministic name and re-requesting it is a cache hit. That gives resumability
across a page reload or a crash for free, which is the only thing a job table would
have bought for a single-user app.
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from .. import batch_spec, config, db, pixelsort_spec, runner
from ..tracking import client_ip

log = logging.getLogger("site.batch")
router = APIRouter(prefix="/api/batch")

BATCH_ID = re.compile(r"^[0-9a-f]{32}$")


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _batch_dir(batch_id: str) -> Path | None:
    """Resolve a batch directory, refusing anything that isn't a plain hex id."""
    if not BATCH_ID.match(batch_id or ""):
        return None
    path = config.BATCH_DIR / batch_id
    return path if path.is_dir() else None


def _load(batch_id: str) -> dict | None:
    directory = _batch_dir(batch_id)
    if directory is None:
        return None
    try:
        return json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _annotate(record: dict) -> dict:
    """Attach the display command and any already-cached result to each cell."""
    source = Path(record["source_path"])
    for cell in record["plan"]["cells"]:
        params = pixelsort_spec.normalize(cell["params"])
        output = runner.result_path_for(params, source, prefix="cell")
        cell["command"] = runner.display_command(
            pixelsort_spec.build_argv(params, str(source), str(output))
        )
        if output.exists():
            cell["url"] = f"/results/{output.name}"
            cell["view_url"] = f"/results/{output.with_suffix('.view.webp').name}"
            cell["cached"] = True
    return record


@router.post("")
async def create_batch(
    request: Request,
    image: UploadFile = File(...),
    mode: str = Form("matrix"),
    grid_size: int = Form(4),
    interval_function: str = Form("threshold"),
    sorting_function: str = Form("lightness"),
    sweep_param: str = Form("angle"),
    angle: float = Form(0.0),
    randomness: float = Form(0.0),
    lower_threshold: float = Form(0.25),
    upper_threshold: float = Form(0.8),
    clength: int = Form(50),
):
    """Store the source once and return the full plan with nothing rendered yet.

    Returning immediately is the point: the grid draws 16 labelled skeletons and a
    time estimate before any sorting starts, so the user can read what is coming.
    """
    batch_id = uuid.uuid4().hex
    directory = config.BATCH_DIR / batch_id
    directory.mkdir(parents=True, exist_ok=True)

    try:
        source, width, height = runner.normalize_full_size(
            await image.read(), directory / "source.png"
        )
    except runner.ImageRejected as exc:
        return _error(str(exc))

    constants = {
        "interval_function": interval_function,
        "sorting_function": sorting_function,
        "angle": angle,
        "randomness": randomness,
        "lower_threshold": lower_threshold,
        "upper_threshold": upper_threshold,
        "clength": clength,
    }

    try:
        plan = batch_spec.build_plan(mode, grid_size, {
            "interval_function": interval_function,
            "sorting_function": sorting_function,
            "sweep_param": sweep_param,
            "constants": constants,
        })
        # Fail now rather than on cell 9 of 16.
        for cell in plan["cells"]:
            pixelsort_spec.normalize(cell["params"])
    except (batch_spec.PlanError, pixelsort_spec.ParamError) as exc:
        return _error(str(exc))

    megapixels = width * height / 1e6
    record = {
        "batch_id": batch_id,
        "source_path": str(source),
        "source_url": f"/batches/{batch_id}/source.png",
        "width": width,
        "height": height,
        "megapixels": round(megapixels, 2),
        "concurrency": config.BATCH_CONCURRENCY,
        "estimate_seconds": batch_spec.estimate_seconds(
            len(plan["cells"]), megapixels, config.BATCH_CONCURRENCY
        ),
        "fill_order": batch_spec.fill_order(plan["grid_size"]),
        "constants": constants,
        "plan": plan,
    }
    (directory / "plan.json").write_text(json.dumps(record), encoding="utf-8")

    runner.clear_cancel(batch_id)
    return {"ok": True, **_annotate(record)}


@router.get("/{batch_id}")
async def get_batch(batch_id: str):
    """Rehydrate a batch. Completed cells come back immediately.

    Fetching a batch also clears any cancel flag, so this doubles as the resume
    step: a stopped batch stays stopped until the user asks for it again, and
    finished cells are reused rather than recomputed.
    """
    record = _load(batch_id)
    if record is None:
        return _error("That batch no longer exists.", 404)
    runner.clear_cancel(batch_id)
    return {"ok": True, **_annotate(record)}


@router.get("/{batch_id}/cell/{index}")
async def render_cell(request: Request, batch_id: str, index: int):
    record = _load(batch_id)
    if record is None:
        return _error("That batch no longer exists.", 404)
    if runner.is_cancelled(batch_id):
        return _error("This batch was cancelled.", 409)

    cells = record["plan"]["cells"]
    if not 0 <= index < len(cells):
        return _error("No such cell.", 404)

    source = Path(record["source_path"])
    if not source.exists():
        return _error("The source image for this batch is gone.", 410)

    try:
        params = pixelsort_spec.normalize(cells[index]["params"])
    except pixelsort_spec.ParamError as exc:
        return _error(str(exc))

    output = runner.result_path_for(params, source, prefix="cell")
    argv = pixelsort_spec.build_argv(params, str(source), str(output))
    command = runner.display_command(argv)

    if output.exists():
        view = runner.write_view_derivative(output)
        return {"ok": True, "index": index, "cached": True, "duration_ms": 0,
                "url": f"/results/{output.name}", "view_url": f"/results/{view.name}",
                "command": command, "width": record["width"], "height": record["height"]}

    ip = client_ip(request)
    try:
        argv, duration = await runner.run_pixelsort(
            params, source, output, batch_id=batch_id
        )
    except runner.SortCancelled:
        return _error("This batch was cancelled.", 409)
    except runner.SortFailed as exc:
        db.record_job(ip, "batch", command, 0, "error")
        return _error(str(exc), 500)

    view = runner.write_view_derivative(output)
    db.record_job(ip, "batch", command, duration, "ok")
    return {"ok": True, "index": index, "cached": False, "duration_ms": duration,
            "url": f"/results/{output.name}", "view_url": f"/results/{view.name}",
            "command": runner.display_command(argv),
            "width": record["width"], "height": record["height"]}


@router.post("/{batch_id}/cancel")
async def cancel(batch_id: str):
    if _batch_dir(batch_id) is None:
        return _error("That batch no longer exists.", 404)
    return {"ok": True, "killed": runner.cancel_batch(batch_id)}


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "cell"


@router.get("/{batch_id}/download.zip")
async def download_zip(batch_id: str):
    """Zip the full-size renders, not the display derivatives."""
    record = _load(batch_id)
    if record is None:
        return _error("That batch no longer exists.", 404)

    source = Path(record["source_path"])
    plan = record["plan"]
    buffer = io.BytesIO()
    manifest = [
        f"Pixelsort batch {batch_id}",
        f"mode={plan['mode']}  grid={plan['grid_size']}x{plan['grid_size']}  "
        f"source={record['width']}x{record['height']}",
        "",
    ]
    included = 0

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for cell in plan["cells"]:
            params = pixelsort_spec.normalize(cell["params"])
            output = runner.result_path_for(params, source, prefix="cell")
            if not output.exists():
                continue
            argv = pixelsort_spec.build_argv(params, str(source), str(output))
            label = "_".join(filter(None, [cell.get("row_label"), cell.get("col_label")]))
            name = f"{cell['index']:02d}_{_safe(label)}.png"
            archive.write(output, name)
            manifest.append(f"{name}\n    {runner.display_command(argv)}")
            included += 1

        if source.exists():
            archive.write(source, "00_source.png")
        archive.writestr("commands.txt", "\n".join(manifest) + "\n")

    if included == 0:
        return _error("Nothing has finished rendering yet.", 409)

    buffer.seek(0)
    # PNGs are already compressed, so ZIP_STORED keeps this fast and the size is
    # roughly the sum of the parts.
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pixelsort-batch-{batch_id[:8]}.zip"'},
    )
