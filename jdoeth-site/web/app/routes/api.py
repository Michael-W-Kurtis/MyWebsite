"""JSON API backing the two interactive surfaces."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import config, db, pixelsort_spec, runner
from ..tracking import client_ip

log = logging.getLogger("site.api")
router = APIRouter(prefix="/api")

PORTRAIT = config.BASE_DIR / "static" / "img" / "portrait.jpg"


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.post("/pixelsort-me")
async def pixelsort_me(request: Request):
    """The landing-page action. Sorts the owner portrait with two exposed knobs."""
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}

    try:
        params = pixelsort_spec.normalize(
            {
                "interval_function": "threshold",
                "sorting_function": payload.get("sorting_function", "lightness"),
                "angle": payload.get("angle", 0),
                # Canned values chosen because they read well on a portrait:
                # a wide-open threshold band produces long vertical smears without
                # dissolving the face entirely.
                "lower_threshold": 0.20,
                "upper_threshold": 0.85,
            }
        )
    except pixelsort_spec.ParamError as exc:
        return _error(str(exc))

    if not PORTRAIT.exists():
        return _error("The portrait image is missing from static/img/portrait.jpg.", 500)

    output = runner.result_path_for(params, PORTRAIT, prefix="portrait")
    ip = client_ip(request)

    if output.exists():
        argv = pixelsort_spec.build_argv(params, str(PORTRAIT), str(output))
        return {
            "ok": True,
            "cached": True,
            "url": f"/results/{output.name}",
            "command": runner.display_command(argv),
            "duration_ms": 0,
        }

    try:
        argv, duration = await runner.run_pixelsort(params, PORTRAIT, output)
    except runner.SortFailed as exc:
        db.record_job(ip, "portrait", "", 0, "error")
        return _error(str(exc), 500)

    command = runner.display_command(argv)
    db.record_job(ip, "portrait", command, duration, "ok")
    return {
        "ok": True,
        "cached": False,
        "url": f"/results/{output.name}",
        "command": command,
        "duration_ms": duration,
    }


@router.post("/sort")
async def sort_upload(
    request: Request,
    image: UploadFile = File(...),
    interval_function: str = Form("threshold"),
    sorting_function: str = Form("lightness"),
    angle: float = Form(0.0),
    randomness: float = Form(0.0),
    lower_threshold: float = Form(0.25),
    upper_threshold: float = Form(0.8),
    clength: int = Form(50),
    interval_file: UploadFile | None = File(None),
):
    """The wrapper page's SORT action."""
    try:
        params = pixelsort_spec.normalize(
            {
                "interval_function": interval_function,
                "sorting_function": sorting_function,
                "angle": angle,
                "randomness": randomness,
                "lower_threshold": lower_threshold,
                "upper_threshold": upper_threshold,
                "clength": clength,
            }
        )
    except pixelsort_spec.ParamError as exc:
        return _error(str(exc))

    needs_file = params["interval_function"] in ("file", "file-edges")
    if needs_file and interval_file is None:
        return _error(
            f"The '{params['interval_function']}' interval function needs an "
            "interval image. Add one or choose a different interval function."
        )

    try:
        source = runner.normalize_image(await image.read(), config.UPLOAD_DIR)
    except runner.ImageRejected as exc:
        return _error(str(exc))

    interval_path: Path | None = None
    if needs_file:
        try:
            interval_path = runner.normalize_image(
                await interval_file.read(), config.UPLOAD_DIR
            )
        except runner.ImageRejected as exc:
            return _error(f"Interval image: {exc}")
        # pixelsort requires the interval image to match the input dimensions.
        from PIL import Image

        with Image.open(source) as src, Image.open(interval_path) as ivl:
            if ivl.size != src.size:
                resized = ivl.resize(src.size, Image.NEAREST)
                resized.save(interval_path, format="PNG")

    output = runner.result_path_for(params, source, prefix="sorted")
    ip = client_ip(request)

    try:
        argv, duration = await runner.run_pixelsort(params, source, output, interval_path)
    except runner.SortFailed as exc:
        db.record_job(ip, "upload", "", 0, "error")
        return _error(str(exc), 500)

    command = runner.display_command(argv)
    db.record_job(ip, "upload", command, duration, "ok")
    return {
        "ok": True,
        "original_url": f"/uploads/{source.name}",
        "url": f"/results/{output.name}",
        "command": command,
        "duration_ms": duration,
    }
