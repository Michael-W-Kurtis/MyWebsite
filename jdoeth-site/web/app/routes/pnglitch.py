"""PNGlitch wrapper API.

One endpoint runs a glitch; a second re-decodes a result that the browser
refused to render. The rescue pass is on demand rather than automatic because
most glitched PNGs display fine, and re-encoding every result would double the
work for a case that usually does not arise.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import config, db, pnglitch_spec, runner
from ..tracking import client_ip

log = logging.getLogger("site.pnglitch")
router = APIRouter(prefix="/api/pnglitch")

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _result_path(cfg: dict, source: Path) -> Path:
    """Deterministic name, so an identical request is a cache hit.

    Unlike the pixelsort tools this is genuinely reproducible: any randomness is
    in the Ruby we generate, and it is explicitly seeded.
    """
    try:
        fingerprint = f"{source.name}:{source.stat().st_mtime_ns}"
    except OSError:
        fingerprint = source.name
    key = fingerprint + "|" + repr(sorted(cfg.items()))
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return config.GLITCH_DIR / f"glitch-{digest}.png"


@router.post("")
async def glitch(
    request: Request,
    image: UploadFile = File(...),
    operation: str = Form("substitute"),
    filter_type: str = Form("keep"),
    seed: int = Form(1),
    pattern: str = Form("digits"),
    replacement: str = Form("x"),
    count: int = Form(120),
    swaps: int = Form(6),
    block: int = Form(256),
    start_pct: int = Form(30),
    span: int = Form(40),
    period: int = Form(1),
):
    try:
        cfg = pnglitch_spec.normalize({
            "operation": operation, "filter_type": filter_type, "seed": seed,
            "pattern": pattern, "replacement": replacement, "count": count,
            "swaps": swaps, "block": block, "start_pct": start_pct,
            "span": span, "period": period,
        })
    except pnglitch_spec.OperationError as exc:
        return _error(str(exc))

    try:
        source, converted = runner.normalize_png(
            await image.read(), config.GLITCH_DIR / f"src-{_upload_id(image.filename)}.png"
        )
    except runner.ImageRejected as exc:
        return _error(str(exc))

    output = _result_path(cfg, source)
    script = pnglitch_spec.build_script(cfg, str(source), str(output))
    shown = pnglitch_spec.display_script(cfg)
    ip = client_ip(request)

    if output.exists():
        info = runner.inspect_png(output)
        return {"ok": True, "cached": True, "duration_ms": 0,
                "url": f"/glitches/{output.name}", "original_url": f"/glitches/{source.name}",
                "script": shown, "converted": converted, "seed": cfg["seed"], **info}

    try:
        duration = await runner.run_ruby(script, output.with_suffix(".rb"))
    except runner.RubyMissing as exc:
        return _error(str(exc), 503)
    except runner.SortFailed as exc:
        db.record_job(ip, "pnglitch", cfg["operation"], 0, "error")
        return _error(str(exc), 500)

    info = runner.inspect_png(output)
    db.record_job(ip, "pnglitch", cfg["operation"], duration, "ok")
    return {"ok": True, "cached": False, "duration_ms": duration,
            "url": f"/glitches/{output.name}", "original_url": f"/glitches/{source.name}",
            "script": shown, "converted": converted, "seed": cfg["seed"], **info}


@router.get("/rescue/{name}")
async def rescue(name: str):
    """Re-decode a result the browser would not render."""
    if not SAFE_NAME.match(name or "") or not name.endswith(".png"):
        return _error("Not found.", 404)
    target = (config.GLITCH_DIR / name).resolve()
    if config.GLITCH_DIR.resolve() not in target.parents or not target.is_file():
        return _error("Not found.", 404)

    rescued = runner.rescue_png(target)
    if rescued is None:
        return _error(
            "This one is past rescuing - the image data itself is gone, not just "
            "the container. Try a gentler setting or the 'none' filter type.", 422
        )
    return {"ok": True, "url": f"/glitches/{rescued.name}"}


def _upload_id(filename: str | None) -> str:
    stem = Path(filename or "upload").stem[:32]
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-") or "upload"
    return safe
