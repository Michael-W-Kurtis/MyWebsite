"""Environment-driven configuration. Every value here is overridable in .env."""

from __future__ import annotations

import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


BASE_DIR = Path(__file__).resolve().parent
CONTENT_DIR = Path(os.getenv("CONTENT_DIR", BASE_DIR.parent / "content"))

# Writable paths. In Docker these are volume mounts; locally they are plain dirs.
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR.parent / "data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "UploadedImages"))
RESULT_DIR = Path(os.getenv("RESULT_DIR", DATA_DIR / "results"))
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "db" / "site.db"))

OWNER_NAME = os.getenv("OWNER_NAME", "Michael W Kurtis")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "mwmkurtis@gmail.com")
GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/Michael-W-Kurtis")

# --- Image safety -----------------------------------------------------------
# pixelsort runs a pure-Python per-pixel loop. Measured on a modern core it is
# roughly 4 microseconds per pixel: 0.9s at 600x400, 8.4s at 1600x1200, and about
# 50s for a 12MP phone photo. Uploads are therefore downscaled before sorting.
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_BYTES", 12 * 1024 * 1024)
MAX_IMAGE_DIM = _int("MAX_IMAGE_DIM", 1600)      # longest edge, in pixels
MAX_DECODE_PIXELS = _int("MAX_DECODE_PIXELS", 50_000_000)  # decompression-bomb guard

# --- Job execution ----------------------------------------------------------
JOB_TIMEOUT_SECONDS = _int("JOB_TIMEOUT_SECONDS", 90)
MAX_CONCURRENT_JOBS = _int("MAX_CONCURRENT_JOBS", 2)

# --- Retention --------------------------------------------------------------
# Uploads and results are disposable. Anything older than this is swept on boot
# and hourly thereafter, so the volume cannot grow without bound.
RETENTION_HOURS = _int("RETENTION_HOURS", 24)

# --- Stats privacy ----------------------------------------------------------
# The brief asks for a public page listing visitor IPs. That is implemented, but
# both of these switches exist because publishing raw IPs is a real disclosure.
# See README "Before you expose this to the internet".
STATS_MASK_IPS = _bool("STATS_MASK_IPS", False)
STATS_TOKEN = os.getenv("STATS_TOKEN", "").strip()

# Only trust X-Real-IP when the direct peer is one of these. Otherwise any client
# could forge its own address into the stats table.
TRUSTED_PROXIES = [
    cidr.strip()
    for cidr in os.getenv("TRUSTED_PROXIES", "172.16.0.0/12,10.0.0.0/8,192.168.0.0/16,127.0.0.1/32").split(",")
    if cidr.strip()
]

for _d in (UPLOAD_DIR, RESULT_DIR, DB_PATH.parent):
    _d.mkdir(parents=True, exist_ok=True)
