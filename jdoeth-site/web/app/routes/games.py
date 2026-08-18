"""Game pages and Unity build asset delivery.

The build files are served by the app rather than straight off disk by nginx.
That is a deliberate trade: nginx would be faster, but nginx cannot tell whether
a given `.unityweb` is gzip or brotli, and guessing wrong is the single most
common reason a Unity WebGL build hangs on the progress bar. Python can read the
first two bytes and get it right every time. For a personal site serving a
handful of files per session, correctness is worth more than the throughput.

nginx still streams the response through with buffering off, so a 40 MB payload
is not staged to disk on the way past.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import config, games

log = logging.getLogger("site.games")
router = APIRouter()
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


def _context(request: Request, page: str) -> dict:
    from datetime import datetime, timezone
    return {
        "page": page,
        "owner": config.OWNER_NAME,
        "email": config.OWNER_EMAIL,
        "github_url": config.GITHUB_URL,
        "year": datetime.now(timezone.utc).year,
    }


@router.get("/games", response_class=HTMLResponse)
async def games_index(request: Request):
    ctx = _context(request, "games")
    ctx["games"] = games.list_games()
    ctx["games_dir"] = "web/content/games"
    return templates.TemplateResponse(request, "games.html", ctx)


@router.get("/games/{slug}", response_class=HTMLResponse)
async def play(request: Request, slug: str):
    game = games.load_game(slug)
    if game is None:
        ctx = _context(request, "404")
        return templates.TemplateResponse(request, "404.html", ctx, status_code=404)
    ctx = _context(request, "game")
    ctx["game"] = game
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/games/{slug}/build/{path:path}")
async def build_asset(slug: str, path: str):
    return _serve(slug, f"build/{path}")


@router.get("/games/{slug}/assets/{path:path}")
async def game_asset(slug: str, path: str):
    """Anything else in the game folder: TemplateData, StreamingAssets, cover art."""
    return _serve(slug, path)


def _serve(slug: str, relative: str):
    target = games.resolve_asset(slug, relative)
    if target is None:
        return HTMLResponse("Not found", status_code=404)

    headers = {
        # Build files are immutable for a given build; re-uploading changes the
        # bytes but the admin can hard-refresh.
        "Cache-Control": "public, max-age=86400",
        "X-Content-Type-Options": "nosniff",
    }
    encoding = games.detect_encoding(target)
    if encoding:
        headers["Content-Encoding"] = encoding
        # Content-Length is the on-disk (encoded) length, which is what
        # FileResponse reports, and is correct for an encoded body.
        headers["Vary"] = "Accept-Encoding"

    return FileResponse(
        target,
        media_type=games.content_type(target),
        headers=headers,
    )
