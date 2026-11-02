"""Policy for third-party listing media (photos, raw payloads).

Keeps recommendation behaviour unchanged while reducing how long we retain
re-hosted seller photos and raw marketplace payloads.
"""

from __future__ import annotations

import os


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def persist_listing_images(source: str | None) -> bool:
    """Whether to write listing photos to disk / R2 / Supabase.

    Vinted is never persisted. Other catalogue sources default off and can be
    enabled with QUENYA_PERSIST_RETAIL_IMAGES=1.
    """
    if (source or "").strip().lower() == "vinted":
        return False
    return _truthy(os.getenv("QUENYA_PERSIST_RETAIL_IMAGES"), default=False)


def hotlink_listing_images(source: str | None) -> bool:
    """Serve the marketplace CDN URL instead of /img/<hash>.jpg."""
    return not persist_listing_images(source)


def store_listing_previews(source: str | None) -> bool:
    """Tiny blur-up previews are derived from image bytes; skip when ephemeral."""
    return persist_listing_images(source)


def llm_may_receive_listing_photo(source: str | None) -> bool:
    """External LLM tagger must not see Vinted photos unless explicitly enabled."""
    if (source or "").strip().lower() != "vinted":
        return True
    return _truthy(os.getenv("QUENYA_LLM_TAG_VINTED_IMAGES"), default=False)


def vinted_retention_days() -> int:
    raw = os.getenv("QUENYA_VINTED_RETENTION_DAYS", "14").strip() or "14"
    days = int(raw)
    if days < 1:
        raise ValueError("QUENYA_VINTED_RETENTION_DAYS must be >= 1")
    return days


def vinted_request_interval_s() -> float:
    raw = os.getenv("QUENYA_VINTED_REQUEST_INTERVAL_S", "1.0").strip() or "1.0"
    interval = float(raw)
    if interval < 0:
        raise ValueError("QUENYA_VINTED_REQUEST_INTERVAL_S must be >= 0")
    return interval


IDENTIFYING_IMAGE_UA = (
    "QuenyaBot/1.0 (+https://quenya.co.uk; hello@quenya.co.uk)"
)
