"""Application entry point.

Run locally with:   uvicorn app.main:app --reload --port 8000
In Docker the web container runs the same command without --reload.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, runner
from .routes import api, batch, games, pages
from .tracking import VisitTrackingMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("site")


async def _sweeper():
    """Hourly cleanup of expired uploads and results."""
    while True:
        try:
            runner.sweep_old_files()
            runner.sweep_batches()
        except Exception:
            log.exception("Sweep failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    runner.sweep_old_files()
    runner.sweep_batches()
    task = asyncio.create_task(_sweeper())
    log.info("Ready. db=%s uploads=%s", config.DB_PATH, config.UPLOAD_DIR)
    yield
    task.cancel()


app = FastAPI(title=f"{config.OWNER_NAME} \u2014 personal site", lifespan=lifespan)
app.add_middleware(VisitTrackingMiddleware)

app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")
# In the Docker stack nginx serves these two paths directly and requests never
# reach the app. The mounts exist so `uvicorn app.main:app` alone is fully usable.
app.mount("/uploads", StaticFiles(directory=str(config.UPLOAD_DIR)), name="uploads")
app.mount("/results", StaticFiles(directory=str(config.RESULT_DIR)), name="results")
app.mount("/batches", StaticFiles(directory=str(config.BATCH_DIR)), name="batches")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(batch.router)
app.include_router(games.router)

_templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


@app.exception_handler(404)
async def not_found(request: Request, exc):
    ctx = {
        "page": "404",
        "owner": config.OWNER_NAME,
        "email": config.OWNER_EMAIL,
        "github_url": config.GITHUB_URL,
        "year": datetime.now(timezone.utc).year,
    }
    return _templates.TemplateResponse(request, "404.html", ctx, status_code=404)
