"""Visitor identification and visit recording.

The security-relevant part is client_ip(). X-Real-IP is a header, and headers are
client-controlled. If the app trusted it unconditionally, anyone could poison the
stats table with arbitrary addresses by sending `X-Real-IP: 1.2.3.4`. So the header
is only honoured when the TCP peer is one of the configured trusted proxies, which
in this stack means the nginx container on the internal Docker network.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware

from . import config, db

log = logging.getLogger("site.tracking")

_TRUSTED = []
for _cidr in config.TRUSTED_PROXIES:
    try:
        _TRUSTED.append(ipaddress.ip_network(_cidr, strict=False))
    except ValueError:
        log.warning("Ignoring malformed TRUSTED_PROXIES entry: %s", _cidr)

# Paths whose traffic is recorded. Static assets, health checks and API calls are
# excluded so the counts mean "pages a human looked at" rather than "HTTP requests".
TRACKED_PATHS = {"/", "/resume", "/projects", "/projects/pixelsort", "/projects/pixelsort-batch", "/stats", "/github"}


def _is_trusted(addr: str) -> bool:
    try:
        parsed = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(parsed in net for net in _TRUSTED)


def client_ip(request) -> str:
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted(peer):
        return peer

    real = request.headers.get("x-real-ip")
    if real:
        candidate = real.strip()
    else:
        forwarded = request.headers.get("x-forwarded-for", "")
        # Leftmost entry is the original client, but only as trustworthy as the
        # chain that appended it. With a single trusted proxy this is correct.
        candidate = forwarded.split(",")[0].strip()

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return peer
    return candidate


class VisitTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if request.method == "GET" and path in TRACKED_PATHS and response.status_code < 400:
            try:
                db.record_visit(
                    client_ip(request), path, request.headers.get("user-agent")
                )
            except Exception:
                # Never let analytics break page delivery.
                log.exception("Failed to record visit for %s", path)
        return response
