import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

import sentry_sdk
from fastapi import HTTPException

from app import db

logger = logging.getLogger(__name__)
_SQLITE_CLAIM_LOCK = Lock()
_STALE_AFTER = timedelta(minutes=5)
_MAX_ATTEMPTS = 2
_TIMEOUT_ERROR = {"status_code": 504, "detail": "Outfit job timed out"}


def _is_postgres(connection: Any) -> bool:
    return hasattr(connection, "acquire_advisory_xact_lock")


def _decode_job(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    job = dict(row)
    for field in ("payload", "result", "error"):
        value = job.pop(f"{field}_json", None)
        job[field] = json.loads(value) if value is not None else None
    return job


def enqueue(
    kind: str,
    owner_id: str | None,
    profile_id: str | None,
    payload: dict[str, Any],
) -> str:
    db.init_db()
    job_id = uuid.uuid4().hex
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO jobs
               (id, kind, owner_id, profile_id, status, payload_json, created_at)
               VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
            (
                job_id,
                kind,
                owner_id,
                profile_id,
                json.dumps(payload, ensure_ascii=False),
                db.now_iso(),
            ),
        )
    return job_id


def get(job_id: str, owner_id: str | None) -> dict[str, Any] | None:
    db.init_db()
    owner_clause = "owner_id IS NULL" if owner_id is None else "owner_id=?"
    params = (job_id,) if owner_id is None else (job_id, owner_id)
    with db.db_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM jobs WHERE id=? AND {owner_clause}",
            params,
        ).fetchone()
    return _decode_job(row)


def claim_next(kinds: list[str], worker_id: str) -> dict[str, Any] | None:
    if not kinds:
        return None
    db.init_db()
    with db.db_connection() as connection:
        if _is_postgres(connection):
            row = connection.execute(
                """UPDATE jobs
                   SET status='running', worker_id=?, attempts=attempts + 1,
                       started_at=now(), heartbeat_at=now(), finished_at=NULL
                   WHERE id=(
                       SELECT id FROM jobs
                       WHERE status='queued' AND kind = ANY(?)
                       ORDER BY created_at
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1
                   )
                   RETURNING *""",
                (worker_id, kinds),
            ).fetchone()
        else:
            with _SQLITE_CLAIM_LOCK:
                connection.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in kinds)
                row = connection.execute(
                    f"""SELECT id FROM jobs
                        WHERE status='queued' AND kind IN ({placeholders})
                        ORDER BY created_at LIMIT 1""",
                    kinds,
                ).fetchone()
                if row is None:
                    return None
                job_id = row["id"]
                connection.execute(
                    """UPDATE jobs
                       SET status='running', worker_id=?, attempts=attempts + 1,
                           started_at=?, heartbeat_at=?, finished_at=NULL
                       WHERE id=? AND status='queued'""",
                    (worker_id, db.now_iso(), db.now_iso(), job_id),
                )
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
    return _decode_job(row)


def claim(job_id: str, worker_id: str) -> dict[str, Any] | None:
    db.init_db()
    with db.db_connection() as connection:
        if _is_postgres(connection):
            row = connection.execute(
                """UPDATE jobs
                   SET status='running', worker_id=?, attempts=attempts + 1,
                       started_at=now(), heartbeat_at=now(), finished_at=NULL
                   WHERE id=? AND status='queued'
                   RETURNING *""",
                (worker_id, job_id),
            ).fetchone()
        else:
            with _SQLITE_CLAIM_LOCK:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    """UPDATE jobs
                       SET status='running', worker_id=?, attempts=attempts + 1,
                           started_at=?, heartbeat_at=?, finished_at=NULL
                       WHERE id=? AND status='queued'""",
                    (worker_id, db.now_iso(), db.now_iso(), job_id),
                )
                # Some sqlite wrappers omit rowcount; re-read and trust status.
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if row is None or dict(row).get("status") != "running":
                    row = None
                del updated
    return _decode_job(row)


def heartbeat(job_id: str) -> bool:
    db.init_db()
    with db.db_connection() as connection:
        result = connection.execute(
            """UPDATE jobs SET heartbeat_at=?
               WHERE id=? AND status='running'""",
            (db.now_iso(), job_id),
        )
    return result.rowcount > 0


def complete(job_id: str, result: dict[str, Any]) -> None:
    db.init_db()
    with db.db_connection() as connection:
        connection.execute(
            """UPDATE jobs
               SET status='done', result_json=?, error_json=NULL,
                   finished_at=?, heartbeat_at=?
               WHERE id=?""",
            (
                json.dumps(result, ensure_ascii=False),
                db.now_iso(),
                db.now_iso(),
                job_id,
            ),
        )


def fail(job_id: str, status_code: int, detail: Any) -> None:
    db.init_db()
    with db.db_connection() as connection:
        connection.execute(
            """UPDATE jobs
               SET status='error', error_json=?, result_json=NULL,
                   finished_at=?, heartbeat_at=?
               WHERE id=?""",
            (
                json.dumps(
                    {"status_code": status_code, "detail": detail},
                    ensure_ascii=False,
                ),
                db.now_iso(),
                db.now_iso(),
                job_id,
            ),
        )


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp


def requeue_stale() -> int:
    db.init_db()
    changed = 0
    with db.db_connection() as connection:
        if _is_postgres(connection):
            requeued = connection.execute(
                """UPDATE jobs
                   SET status='queued', worker_id=NULL, started_at=NULL,
                       heartbeat_at=NULL, finished_at=NULL, error_json=NULL
                   WHERE status='running'
                     AND heartbeat_at < now() - interval '5 minutes'
                     AND attempts < ?""",
                (_MAX_ATTEMPTS,),
            )
            failed = connection.execute(
                """UPDATE jobs
                   SET status='error', worker_id=NULL, error_json=?,
                       finished_at=now()
                   WHERE status='running'
                     AND heartbeat_at < now() - interval '5 minutes'
                     AND attempts >= ?""",
                (json.dumps(_TIMEOUT_ERROR), _MAX_ATTEMPTS),
            )
            changed = requeued.rowcount + failed.rowcount
        else:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC)
            stale = connection.execute(
                """SELECT id, attempts, heartbeat_at FROM jobs
                   WHERE status='running'"""
            ).fetchall()
            for row in stale:
                heartbeat_at = _parse_timestamp(row["heartbeat_at"])
                if heartbeat_at is None or now - heartbeat_at <= _STALE_AFTER:
                    continue
                if row["attempts"] < _MAX_ATTEMPTS:
                    updated = connection.execute(
                        """UPDATE jobs
                           SET status='queued', worker_id=NULL, started_at=NULL,
                               heartbeat_at=NULL, finished_at=NULL, error_json=NULL
                           WHERE id=? AND status='running'""",
                        (row["id"],),
                    )
                else:
                    updated = connection.execute(
                        """UPDATE jobs
                           SET status='error', worker_id=NULL, error_json=?,
                               finished_at=?
                           WHERE id=? AND status='running'""",
                        (json.dumps(_TIMEOUT_ERROR), db.now_iso(), row["id"]),
                    )
                changed += updated.rowcount
    return changed


def stats() -> dict[str, int | float | None]:
    db.init_db()
    with db.db_connection() as connection:
        row = connection.execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END), 0)
                       AS queued,
                   COALESCE(SUM(CASE WHEN status='running' THEN 1 ELSE 0 END), 0)
                       AS running,
                   MIN(CASE WHEN status='queued' THEN created_at END)
                       AS oldest_queued
               FROM jobs"""
        ).fetchone()
    queued = int(row["queued"])
    running = int(row["running"])
    oldest = _parse_timestamp(row["oldest_queued"])
    oldest_queued_s = (
        max(0.0, (datetime.now(UTC) - oldest).total_seconds())
        if oldest is not None
        else None
    )
    return {
        "queued": queued,
        "running": running,
        "oldest_queued_s": oldest_queued_s,
    }


def _load_owner(owner_id: str | None) -> dict[str, Any] | None:
    if owner_id is None:
        return None
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id=?",
            (owner_id,),
        ).fetchone()
    if row is None:
        return None
    owner = dict(row)
    return None if owner.get("revoked_at") is not None else owner


WORKER_KINDS = ("outfits", "catalogue_fetch", "vinted_refill", "live_tag")


def run_job(job: dict[str, Any]) -> None:
    from app import accounts
    from app.phase2_api import (
        _fetch_profile_items,
        _refill_vinted,
        _tag_pending_live,
        generate_outfits,
    )
    from app.schemas import OutfitRequest

    job_id = job["id"]
    kind = job.get("kind")
    owner = _load_owner(job.get("owner_id"))
    if owner is None:
        fail(job_id, 401, "Sign in with your invite link")
        return
    if kind not in WORKER_KINDS:
        fail(job_id, 400, "Unsupported job kind")
        return

    user_token = accounts._user_context.set(owner)
    request_token = accounts._in_request.set(True)
    try:
        payload = job.get("payload") or {}
        if kind == "outfits":
            result = generate_outfits(OutfitRequest.model_validate(payload))
            complete(job_id, result)
            return
        if kind == "catalogue_fetch":
            _fetch_profile_items(
                payload["fetch_job_id"],
                job["profile_id"],
                payload.get("queries") or [],
                payload.get("gender") or "both",
                payload.get("context") or "other",
            )
            complete(job_id, {"fetch_job_id": payload["fetch_job_id"]})
            return
        if kind == "vinted_refill":
            _refill_vinted(
                payload["fetch_job_id"],
                job["profile_id"],
                int(payload.get("page") or 1),
            )
            complete(job_id, {"fetch_job_id": payload["fetch_job_id"]})
            return
        if kind == "live_tag":
            tagged, failed, errors, domain_counts = _tag_pending_live(
                job["profile_id"],
                source_urls=payload.get("source_urls"),
            )
            complete(
                job_id,
                {
                    "tagged": tagged,
                    "failed": failed,
                    "errors": errors,
                    "domain_counts": domain_counts,
                },
            )
            return
        fail(job_id, 400, "Unsupported job kind")
    except HTTPException as exc:
        fail(job_id, exc.status_code, exc.detail)
    except Exception:
        logger.exception("Job %s (%s) failed", job_id, kind)
        sentry_sdk.capture_exception()
        detail = (
            "Outfit generation failed"
            if kind == "outfits"
            else f"{kind} job failed"
        )
        fail(job_id, 500, detail)
    finally:
        accounts._in_request.reset(request_token)
        accounts._user_context.reset(user_token)
