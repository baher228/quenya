import json
import math
import statistics
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    brand TEXT,
    price_gbp REAL,
    size TEXT,
    description TEXT,
    image_url TEXT NOT NULL,
    image_path TEXT,
    gender_hint TEXT,
    condition_hint TEXT,
    availability TEXT,
    query TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    tags_json TEXT,
    tags_request_id TEXT,
    tagged_at TEXT,
    tag_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_untagged ON items(tags_json, tag_error);
CREATE INDEX IF NOT EXISTS idx_items_tags_source ON items(source, gender_hint, condition_hint);
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY, brief_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swipes (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, item_id TEXT NOT NULL,
    liked INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS taste_versions (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, version INTEGER NOT NULL,
    profile_json TEXT NOT NULL, signals_json TEXT NOT NULL, request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outfits (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, taste_version INTEGER NOT NULL,
    title TEXT NOT NULL, mood TEXT NOT NULL, piece_ids_json TEXT NOT NULL,
    why TEXT NOT NULL, total_gbp REAL, verdict TEXT, reason TEXT, request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, kind TEXT NOT NULL,
    target_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT,
    note TEXT NOT NULL, request_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, image_path TEXT NOT NULL,
    read_json TEXT, request_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL, model_requested TEXT NOT NULL, model_returned TEXT,
    request_id TEXT, latency_ms INTEGER NOT NULL, prompt_tokens INTEGER,
    completion_tokens INTEGER, ok INTEGER NOT NULL, error TEXT,
    prompt_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task_created ON llm_calls(task, created_at);
"""
_INIT_LOCK = Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


@contextmanager
def db_connection():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with _INIT_LOCK:
        with db_connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)


def insert_items(items: list[dict[str, Any]]) -> int:
    init_db()
    inserted = 0
    sql = """
    INSERT OR IGNORE INTO items (
        id, source, source_url, title, brand, price_gbp, size, description,
        image_url, image_path, gender_hint, condition_hint, availability,
        query, fetched_at, raw_json
    ) VALUES (
        :id, :source, :source_url, :title, :brand, :price_gbp, :size, :description,
        :image_url, :image_path, :gender_hint, :condition_hint, :availability,
        :query, :fetched_at, :raw_json
    )
    """
    with db_connection() as connection:
        for item in items:
            row = dict(item)
            row.setdefault("fetched_at", now_iso())
            row.setdefault("image_path", None)
            row.setdefault("description", None)
            row.setdefault("brand", None)
            row.setdefault("size", None)
            row.setdefault("availability", None)
            row.setdefault("query", None)
            row.setdefault("gender_hint", None)
            row.setdefault("condition_hint", None)
            row["raw_json"] = json.dumps(row.get("raw_json", row), ensure_ascii=False)
            cursor = connection.execute(sql, row)
            inserted += cursor.rowcount
    return inserted


def untagged_items(limit: int) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM items WHERE tags_json IS NULL ORDER BY fetched_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_tags(
    item_id: str,
    tags: dict[str, Any],
    request_id: str | None,
    cached_image_path: str | None = None,
) -> None:
    with db_connection() as connection:
        connection.execute(
            """UPDATE items SET tags_json=?, tags_request_id=?, tagged_at=?,
               image_path=COALESCE(?, image_path), tag_error=NULL
               WHERE id=?""",
            (
                json.dumps(tags, ensure_ascii=False),
                request_id,
                now_iso(),
                cached_image_path,
                item_id,
            ),
        )


def save_tag_error(item_id: str, error: str) -> None:
    with db_connection() as connection:
        connection.execute("UPDATE items SET tag_error=? WHERE id=?", (error, item_id))


def log_llm_call(entry: dict[str, Any]) -> None:
    init_db()
    values = {
        "task": entry["task"],
        "model_requested": entry["model_requested"],
        "model_returned": entry.get("model_returned"),
        "request_id": entry.get("request_id"),
        "latency_ms": entry.get("latency_ms", 0),
        "prompt_tokens": entry.get("prompt_tokens"),
        "completion_tokens": entry.get("completion_tokens"),
        "ok": int(bool(entry.get("ok"))),
        "error": entry.get("error"),
        "prompt_version": entry.get("prompt_version", "unknown"),
        "created_at": entry.get("created_at", now_iso()),
    }
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO llm_calls (task, model_requested, model_returned, request_id,
               latency_ms, prompt_tokens, completion_tokens, ok, error, prompt_version,
               created_at) VALUES (:task, :model_requested, :model_returned, :request_id,
               :latency_ms, :prompt_tokens, :completion_tokens, :ok, :error,
               :prompt_version, :created_at)""",
            values,
        )


def get_item(item_id: str) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        row = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return dict(row) if row else None


def total_counts() -> tuple[int, int]:
    init_db()
    with db_connection() as connection:
        total, tagged = connection.execute(
            "SELECT COUNT(*), SUM(tags_json IS NOT NULL) FROM items"
        ).fetchone()
    return int(total), int(tagged or 0)


def list_items(
    *, slot: str | None, gender: str | None, condition: str | None,
    source: str | None, limit: int
) -> list[dict[str, Any]]:
    init_db()
    clauses = ["tags_json IS NOT NULL"]
    params: list[Any] = []
    filters = (
        ("gender_hint", gender),
        ("condition_hint", condition),
        ("source", source),
    )
    for column, value in filters:
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    if not slot:
        params.append(limit)
    sql = f"SELECT * FROM items WHERE {' AND '.join(clauses)} ORDER BY fetched_at DESC"
    if not slot:
        sql += " LIMIT ?"
    with db_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        tags = json.loads(item["tags_json"])
        if slot and tags.get("slot") != slot:
            continue
        item["tags"] = tags
        result.append(item)
        if len(result) == limit:
            break
    return result


def model_stats() -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM llm_calls ORDER BY created_at DESC"
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["task"], []).append(dict(row))
    stats = []
    for task, calls in grouped.items():
        latencies = sorted(call["latency_ms"] for call in calls)
        total_tokens = sum(
            (call["prompt_tokens"] or 0) + (call["completion_tokens"] or 0)
            for call in calls
        )
        stats.append(
            {
                "task": task,
                "calls": len(calls),
                "ok": sum(call["ok"] for call in calls),
                "failed": sum(not call["ok"] for call in calls),
                "p50_latency_ms": _percentile(latencies, 0.5),
                "p95_latency_ms": _percentile(latencies, 0.95),
                "total_tokens": total_tokens,
                "last_request_ids": [call["request_id"] for call in calls[:5]],
            }
        )
    return stats


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    if quantile == 0.5:
        return round(statistics.median(values))
    return values[min(len(values) - 1, max(0, math.ceil(len(values) * quantile) - 1))]
