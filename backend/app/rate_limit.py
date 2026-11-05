"""Durable rate limits shared across API instances."""

from __future__ import annotations

import logging
import random
from typing import Any

from app import db

logger = logging.getLogger(__name__)

PRUNE_PROBABILITY = 0.05


def _ensure_table(connection: Any) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS rate_limit_events (
               bucket TEXT NOT NULL,
               created_at TEXT NOT NULL
           )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rate_limit_bucket_created "
        "ON rate_limit_events(bucket, created_at)"
    )


def reset_bucket(bucket: str | None = None) -> None:
    """Clear one bucket, all buckets with a prefix (trailing ':'), or everything."""
    db.init_db()
    with db.db_connection() as connection:
        _ensure_table(connection)
        if bucket is None:
            connection.execute("DELETE FROM rate_limit_events")
        elif bucket.endswith(":"):
            connection.execute(
                "DELETE FROM rate_limit_events WHERE bucket LIKE ?",
                (f"{bucket}%",),
            )
        else:
            connection.execute(
                "DELETE FROM rate_limit_events WHERE bucket=?",
                (bucket,),
            )


def _cutoff_iso(window_seconds: float, now_iso: str) -> str:
    from datetime import UTC, datetime, timedelta

    now = datetime.fromisoformat(now_iso)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (now - timedelta(seconds=window_seconds)).isoformat()


def _count(connection: Any, bucket: str, cutoff: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM rate_limit_events "
        "WHERE bucket=? AND created_at >= ?",
        (bucket, cutoff),
    ).fetchone()
    return int(row["count"] if row and "count" in row.keys() else row[0])


def is_limited(
    bucket: str,
    *,
    limit: int,
    window_seconds: float,
    now_iso: str | None = None,
) -> bool:
    """Return True when the bucket is already at/over the limit (no write)."""
    db.init_db()
    timestamp = now_iso or db.now_iso()
    with db.db_connection() as connection:
        _ensure_table(connection)
        return (
            _count(connection, bucket, _cutoff_iso(window_seconds, timestamp))
            >= limit
        )


def prune(bucket: str, *, window_seconds: float, now_iso: str | None = None) -> None:
    """Delete a bucket's expired events. Best effort: concurrent prunes may conflict."""
    timestamp = now_iso or db.now_iso()
    try:
        with db.db_connection() as connection:
            connection.execute(
                "DELETE FROM rate_limit_events WHERE bucket=? AND created_at < ?",
                (bucket, _cutoff_iso(window_seconds, timestamp)),
            )
    except Exception:
        logger.warning("rate limit prune failed for %s", bucket, exc_info=True)


def hit(
    bucket: str,
    *,
    now_iso: str | None = None,
    window_seconds: float | None = None,
) -> None:
    """Record one event in the bucket and occasionally prune expired events."""
    db.init_db()
    timestamp = now_iso or db.now_iso()
    with db.db_connection() as connection:
        _ensure_table(connection)
        connection.execute(
            "INSERT INTO rate_limit_events (bucket, created_at) VALUES (?, ?)",
            (bucket, timestamp),
        )
    if window_seconds is not None and random.random() < PRUNE_PROBABILITY:
        prune(bucket, window_seconds=window_seconds, now_iso=timestamp)


def check_and_hit(
    bucket: str,
    *,
    limit: int,
    window_seconds: float,
    now_iso: str | None = None,
) -> bool:
    """Return True when already limited; otherwise record a hit and return False."""
    timestamp = now_iso or db.now_iso()
    if is_limited(
        bucket, limit=limit, window_seconds=window_seconds, now_iso=timestamp
    ):
        return True
    hit(bucket, now_iso=timestamp, window_seconds=window_seconds)
    return False
