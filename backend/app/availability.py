"""Refresh listing availability for recently shown catalogue items."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app import db

logger = logging.getLogger(__name__)

_SOLD_MARKERS = re.compile(
    r"(sold\s*out|out\s*of\s*stock|unavailable|no longer available|item is sold|"
    r"cette annonce n.?est plus disponible|verkauft)",
    re.I,
)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


def _mark_unavailable(item_id: str, reason: str) -> None:
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE items SET availability=? WHERE id=?",
            (reason, item_id),
        )
    db.invalidate_live_catalogue()


def probe_source_url(source_url: str, timeout: float = 8.0) -> str | None:
    """Return an availability marker if the listing looks sold, else None."""
    try:
        response = httpx.get(
            source_url,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html"},
            follow_redirects=True,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        logger.info("availability probe failed: %s", type(exc).__name__)
        return None
    if response.status_code in {404, 410}:
        return "SoldOut"
    if response.status_code >= 400:
        return None
    text = response.text[:200_000]
    if _SOLD_MARKERS.search(text):
        return "SoldOut"
    return None


def refresh_items(items: list[dict[str, Any]], *, limit: int = 3) -> int:
    """Probe up to `limit` Vinted (or other) source URLs and mark sold ones."""
    updated = 0
    checked = 0
    try:
        for item in items:
            if checked >= limit:
                break
            source = (item.get("source") or "").lower()
            source_url = item.get("source_url")
            if not source_url or source not in {"vinted", "retail"}:
                continue
            availability = item.get("availability") or ""
            if any(
                marker in str(availability)
                for marker in ("SoldOut", "OutOfStock", "Discontinued")
            ):
                continue
            checked += 1
            marker = probe_source_url(source_url)
            if marker:
                _mark_unavailable(item["id"], marker)
                updated += 1
    except Exception:
        # Background deck refresh must never crash the request or poison tests.
        logger.exception("availability refresh failed")
    return updated
