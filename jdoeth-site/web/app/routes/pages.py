"""Server-rendered pages."""

from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import batch_spec, config, db, games as games_registry, pixelsort_spec

log = logging.getLogger("site.pages")
router = APIRouter()
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))

RESUME_MD = config.CONTENT_DIR / "resume.md"
RESUME_PDF = config.CONTENT_DIR / "resume.pdf"


def _base_context(page: str) -> dict:
    return {
        "page": page,
        "owner": config.OWNER_NAME,
        "email": config.OWNER_EMAIL,
        "github_url": config.GITHUB_URL,
        "year": datetime.now(timezone.utc).year,
    }


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    ctx = _base_context("home")
    ctx["sorting_functions"] = pixelsort_spec.SORTING_FUNCTIONS
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/resume", response_class=HTMLResponse)
async def resume(request: Request):
    if RESUME_MD.exists():
        body = markdown.markdown(
            RESUME_MD.read_text(encoding="utf-8"),
            extensions=["extra", "sane_lists", "toc"],
        )
        missing = False
    else:
        body = "<p>No resume file found. Add <code>web/content/resume.md</code>.</p>"
        missing = True

    ctx = _base_context("resume")
    ctx["resume_html"] = body
    ctx["resume_missing"] = missing
    ctx["has_pdf"] = RESUME_PDF.exists()
    return templates.TemplateResponse(request, "resume.html", ctx)


@router.get("/resume.pdf")
async def resume_pdf():
    from fastapi.responses import FileResponse

    if not RESUME_PDF.exists():
        return RedirectResponse("/resume", status_code=302)
    return FileResponse(RESUME_PDF, media_type="application/pdf",
                        filename="Jonathan-Doeth-Resume.pdf")


@router.get("/projects", response_class=HTMLResponse)
async def projects(request: Request):
    # Adding a project is a matter of appending one dict here and, if it needs its
    # own page, one route. Cards render from this list.
    catalogue = [
        {
            "slug": "pixelsort-batch",
            "title": "Pixelsort Batch",
            "tagline": "Nine or sixteen full-size renders at once",
            "body": "Sweep two parameters across a grid and see the whole range "
                    "at full resolution, instead of guessing one render at a time.",
            "tags": ["Python", "Parameter sweep", "Glitch art"],
            "href": "/projects/pixelsort-batch",
            "status": "live",
        },
        {
            "slug": "pixelsort",
            "title": "Pixelsort Wrapper",
            "tagline": "A browser front end for satyarth/pixelsort",
            "body": "Upload an image, pick an interval and sorting function, and "
                    "watch the equivalent command assemble itself before it runs.",
            "tags": ["Python", "Pillow", "Glitch art"],
            "href": "/projects/pixelsort",
            "status": "live",
        },
    ]
    # Games are discovered from disk, so dropping a build into
    # web/content/games/<slug>/ makes a card appear with no code change.
    for game in games_registry.list_games():
        catalogue.append({
            "slug": game["slug"],
            "title": game["title"],
            "tagline": game["tagline"] or "Unity WebGL",
            "body": game["description"] or "A browser game.",
            "tags": game["tags"],
            "href": game["href"],
            "status": "live" if game["playable"] else "setup",
        })

    ctx = _base_context("projects")
    ctx["projects"] = catalogue
    return templates.TemplateResponse(request, "projects.html", ctx)


@router.get("/projects/pixelsort", response_class=HTMLResponse)
async def pixelsort_page(request: Request):
    ctx = _base_context("pixelsort")
    ctx["spec"] = pixelsort_spec.spec_for_client()
    ctx["max_dim"] = config.MAX_IMAGE_DIM
    ctx["max_mb"] = config.MAX_UPLOAD_BYTES // (1024 * 1024)
    return templates.TemplateResponse(request, "pixelsort.html", ctx)


@router.get("/projects/pixelsort-batch", response_class=HTMLResponse)
async def pixelsort_batch_page(request: Request):
    ctx = _base_context("pixelsort-batch")
    ctx["spec"] = pixelsort_spec.spec_for_client()
    ctx["batch"] = {
        "gridSizes": list(batch_spec.GRID_SIZES),
        "matrix": {
            "rowValues": batch_spec.MATRIX_DEFAULT["row_values"],
            "colValues": batch_spec.MATRIX_DEFAULT["col_values"],
        },
        "intensityAxes": batch_spec.INTENSITY_AXES,
        "ladders": batch_spec.LADDERS,
        "sweeps": batch_spec.SWEEPS,
        "concurrency": config.BATCH_CONCURRENCY,
        "maxEdge": config.BATCH_MAX_EDGE,
        "secondsPerMegapixel": batch_spec.SECONDS_PER_MEGAPIXEL,
    }
    ctx["max_mb"] = config.MAX_UPLOAD_BYTES // (1024 * 1024)
    return templates.TemplateResponse(request, "batch.html", ctx)


@router.get("/github")
async def github():
    return RedirectResponse(config.GITHUB_URL, status_code=302)


def _mask(ip: str) -> str:
    """Drop the host portion: 203.0.113.47 -> 203.0.113.x, keeping IPv6 to /48."""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if parsed.version == 4:
        return ".".join(ip.split(".")[:3]) + ".x"
    return ":".join(ip.split(":")[:3]) + ":\u2026"


@router.get("/stats", response_class=HTMLResponse)
async def stats(request: Request):
    ctx = _base_context("stats")

    if config.STATS_TOKEN and request.query_params.get("token") != config.STATS_TOKEN:
        ctx["locked"] = True
        return templates.TemplateResponse(request, "stats.html", ctx, status_code=401)

    rows = db.visitor_rows()
    if config.STATS_MASK_IPS:
        for row in rows:
            row["ip"] = _mask(row["ip"])

    ctx["locked"] = False
    ctx["rows"] = rows
    ctx["summary"] = db.summary()
    ctx["masked"] = config.STATS_MASK_IPS
    return templates.TemplateResponse(request, "stats.html", ctx)


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}
