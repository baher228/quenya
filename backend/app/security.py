"""HTTP security helpers (headers + API rate limits)."""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app import rate_limit

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer-when-downgrade",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "cross-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def api_rate_window_s() -> float:
    return float(_int_env("QUENYA_API_RATE_WINDOW_S", 60))


def enforce_user_rate_limit(owner_id: str | None, action: str, *, limit: int) -> None:
    """Raise 429 when the per-user action bucket is exhausted."""
    if not owner_id or limit < 1:
        return
    bucket = f"api:{action}:{owner_id}"
    try:
        limited = rate_limit.check_and_hit(
            bucket,
            limit=limit,
            window_seconds=api_rate_window_s(),
        )
    except Exception:
        logger.warning("rate limit check failed for %s; allowing", bucket, exc_info=True)
        return
    if limited:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, try again later",
        )


def deck_rate_limit() -> int:
    return _int_env("QUENYA_API_RATE_DECK", 60)


def search_rate_limit() -> int:
    return _int_env("QUENYA_API_RATE_SEARCH", 30)


def outfits_rate_limit() -> int:
    return _int_env("QUENYA_API_RATE_OUTFITS", 20)


def invite_rate_limit() -> int:
    return _int_env("QUENYA_API_RATE_INVITE", 20)


def proxy_rate_limit() -> int:
    return _int_env("QUENYA_API_RATE_PROXY", 120)
