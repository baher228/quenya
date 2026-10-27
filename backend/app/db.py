import json
import logging
import math
import os
import re
import statistics
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock, Thread
from time import monotonic
from typing import Any

from app.config import DB_PATH, QUENYA_CATALOGUE_TTL_S
from app.rank import tags_for, tokens

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
    id TEXT PRIMARY KEY, brief_json TEXT NOT NULL, created_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1,
    label TEXT, context TEXT, updated_at TEXT, owner_id TEXT,
    ranker_arm TEXT, deck_request_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'tester', invite_hash TEXT UNIQUE,
    created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    created_at TEXT NOT NULL, last_seen_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS swipes (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, item_id TEXT NOT NULL,
    liked INTEGER NOT NULL, created_at TEXT NOT NULL, consumed_version INTEGER
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
    created_at TEXT NOT NULL, consumed_version INTEGER
);
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, kind TEXT NOT NULL,
    target_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT,
    note TEXT NOT NULL, request_id TEXT, created_at TEXT NOT NULL,
    consumed_version INTEGER
);
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY, profile_id TEXT, image_path TEXT NOT NULL,
    read_json TEXT, request_id TEXT, created_at TEXT NOT NULL,
    consumed_version INTEGER, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS routes (
    id TEXT PRIMARY KEY, profile_kind TEXT NOT NULL, input_kind TEXT NOT NULL,
    input_text TEXT NOT NULL, output_json TEXT NOT NULL, request_id TEXT,
    chosen_board_id TEXT, corrected INTEGER NOT NULL DEFAULT 0,
    corrected_to_json TEXT, created_at TEXT NOT NULL, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL, model_requested TEXT NOT NULL, model_returned TEXT,
    request_id TEXT, latency_ms INTEGER NOT NULL, prompt_tokens INTEGER,
    completion_tokens INTEGER, ok INTEGER NOT NULL, error TEXT,
    prompt_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task_created ON llm_calls(task, created_at);
CREATE TABLE IF NOT EXISTS fetch_jobs (
    id TEXT PRIMARY KEY, profile_id TEXT, state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetch_jobs_profile_updated
ON fetch_jobs(profile_id, updated_at);
CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id TEXT PRIMARY KEY, model TEXT NOT NULL, img BLOB, txt BLOB,
    mix BLOB NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_embeddings (
    profile_id TEXT PRIMARY KEY, model TEXT NOT NULL, brief BLOB,
    brief_hash TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deck_events (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, item_id TEXT NOT NULL,
    arm TEXT NOT NULL, policy TEXT NOT NULL, position INTEGER NOT NULL,
    score REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deck_events_profile_created
ON deck_events(profile_id, created_at);
"""
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    brand TEXT,
    price_gbp DOUBLE PRECISION,
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
    id TEXT PRIMARY KEY, brief_json TEXT NOT NULL, created_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1,
    label TEXT, context TEXT, updated_at TEXT, owner_id TEXT,
    ranker_arm TEXT, deck_request_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'tester', invite_hash TEXT UNIQUE,
    created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    created_at TEXT NOT NULL, last_seen_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS swipes (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, item_id TEXT NOT NULL,
    liked INTEGER NOT NULL, created_at TEXT NOT NULL, consumed_version INTEGER
);
CREATE TABLE IF NOT EXISTS taste_versions (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, version INTEGER NOT NULL,
    profile_json TEXT NOT NULL, signals_json TEXT NOT NULL, request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outfits (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, taste_version INTEGER NOT NULL,
    title TEXT NOT NULL, mood TEXT NOT NULL, piece_ids_json TEXT NOT NULL,
    why TEXT NOT NULL, total_gbp DOUBLE PRECISION, verdict TEXT, reason TEXT, request_id TEXT,
    created_at TEXT NOT NULL, consumed_version INTEGER
);
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, kind TEXT NOT NULL,
    target_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT,
    note TEXT NOT NULL, request_id TEXT, created_at TEXT NOT NULL,
    consumed_version INTEGER
);
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY, profile_id TEXT, image_path TEXT NOT NULL,
    read_json TEXT, request_id TEXT, created_at TEXT NOT NULL,
    consumed_version INTEGER, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS routes (
    id TEXT PRIMARY KEY, profile_kind TEXT NOT NULL, input_kind TEXT NOT NULL,
    input_text TEXT NOT NULL, output_json TEXT NOT NULL, request_id TEXT,
    chosen_board_id TEXT, corrected INTEGER NOT NULL DEFAULT 0,
    corrected_to_json TEXT, created_at TEXT NOT NULL, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    task TEXT NOT NULL, model_requested TEXT NOT NULL, model_returned TEXT,
    request_id TEXT, latency_ms INTEGER NOT NULL, prompt_tokens INTEGER,
    completion_tokens INTEGER, ok INTEGER NOT NULL, error TEXT,
    prompt_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task_created ON llm_calls(task, created_at);
CREATE TABLE IF NOT EXISTS fetch_jobs (
    id TEXT PRIMARY KEY, profile_id TEXT, state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetch_jobs_profile_updated
ON fetch_jobs(profile_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(kind, active);
CREATE INDEX IF NOT EXISTS idx_routes_kind_corrected
ON routes(profile_kind, corrected, created_at);
CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id TEXT PRIMARY KEY, model TEXT NOT NULL, img vector(512), txt vector(512),
    mix vector(512) NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_embeddings (
    profile_id TEXT PRIMARY KEY, model TEXT NOT NULL, brief vector(512),
    brief_hash TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deck_events (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, item_id TEXT NOT NULL,
    arm TEXT NOT NULL, policy TEXT NOT NULL, position INTEGER NOT NULL,
    score DOUBLE PRECISION NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deck_events_profile_created
ON deck_events(profile_id, created_at);
"""
_INIT_LOCK = Lock()
_POSTGRES_INIT_ADVISORY_LOCK_KEY = 7301442001
_INITIALIZED_DATABASES: set[str] = set()
_LIVE_CATALOGUE_LOCK = Lock()
_LIVE_CATALOGUE_CACHE: list[dict[str, Any]] | None = None
_LIVE_CATALOGUE_CACHE_EXPIRES_AT = 0.0
_LIVE_CATALOGUE_REFRESHING = False
_LIVE_CATALOGUE_GENERATION = 0
logger = logging.getLogger(__name__)


class _HybridRow(dict):
    def __init__(self, columns: list[str], values: tuple[Any, ...]):
        super().__init__(zip(columns, values, strict=True))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._values)


def _postgres_row_factory(cursor):
    columns = [column.name for column in cursor.description or ()]
    return lambda values: _HybridRow(columns, values)


class _PostgresConnection:
    def __init__(self, connection):
        self._connection = connection
        self._in_transaction = False

    def acquire_advisory_xact_lock(self, lock_key: int) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (lock_key,),
        )

    @staticmethod
    def _translate(sql: str, params):
        has_params = bool(params)
        if has_params:
            sql = sql.replace("%", "%%")
            if isinstance(params, dict):
                sql = re.sub(
                    r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)",
                    r"%(\1)s",
                    sql,
                )
            else:
                sql = sql.replace("?", "%s")
        ignore_insert = bool(
            re.match(
                r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b",
                sql,
                flags=re.IGNORECASE,
            )
        )
        if ignore_insert:
            sql = re.sub(
                r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b",
                "INSERT INTO",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
            statement = sql.rstrip().removesuffix(";").rstrip()
            if "ON CONFLICT" not in statement.upper():
                sql = statement + " ON CONFLICT DO NOTHING"
        return sql

    def execute(self, sql: str, params=()):
        # SET LOCAL keeps search_path inside this transaction, so it never leaks to
        # other apps sharing a pooled Supabase backend.
        if not self._in_transaction:
            self._connection.execute("SET LOCAL search_path TO quenya, extensions")
            self._in_transaction = True
        return self._connection.execute(self._translate(sql, params), params or None)

    def commit(self):
        self._connection.commit()
        self._in_transaction = False

    def rollback(self):
        self._connection.rollback()
        self._in_transaction = False

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _redact_dsn(value: str) -> str:
    value = re.sub(
        r"(?i)(postgres(?:ql)?://)[^/\s@'\"<>]*@",
        r"\1[REDACTED]@",
        value,
    )
    return re.sub(
        r"(?i)\b(password|sslpassword)\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s]+)",
        r"\1=[REDACTED]",
        value,
    )


def connect_postgres(dsn: str, **kwargs):
    import psycopg

    try:
        return psycopg.connect(dsn, **kwargs)
    except Exception as exc:
        message = _redact_dsn(str(exc))
        if message == str(exc):
            raise
        try:
            safe_exception = type(exc)(message)
        except Exception:
            safe_exception = RuntimeError(message)
        raise safe_exception from None


def connect():
    database_url = os.getenv("QUENYA_DATABASE_URL")
    if database_url:
        connection = connect_postgres(
            database_url,
            prepare_threshold=None,
            row_factory=_postgres_row_factory,
        )
        return _PostgresConnection(connection)

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
    if os.getenv("QUENYA_SKIP_DB_INIT") == "1":
        return
    with _INIT_LOCK:
        database_url = os.getenv("QUENYA_DATABASE_URL")
        database_key = f"postgres:{database_url}" if database_url else f"sqlite:{DB_PATH}"
        if database_key in _INITIALIZED_DATABASES:
            return
        with db_connection() as connection:
            if database_url:
                connection.acquire_advisory_xact_lock(
                    _POSTGRES_INIT_ADVISORY_LOCK_KEY
                )
                connection.execute("CREATE SCHEMA IF NOT EXISTS quenya")
                connection.execute("CREATE SCHEMA IF NOT EXISTS extensions")
                connection.execute(
                    "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions"
                )
                for statement in POSTGRES_SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                for column, definition in {
                    "owner_id": "TEXT",
                    "ranker_arm": "TEXT",
                    "deck_request_count": "INTEGER NOT NULL DEFAULT 0",
                }.items():
                    connection.execute(
                        f"ALTER TABLE profiles ADD COLUMN IF NOT EXISTS {column} {definition}"
                    )
                connection.execute(
                    "ALTER TABLE routes ADD COLUMN IF NOT EXISTS owner_id TEXT"
                )
                connection.execute(
                    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS owner_id TEXT"
                )
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(SCHEMA)
            migrations = {
                "profiles": {
                    "kind": "TEXT NOT NULL DEFAULT 'user'",
                    "active": "INTEGER NOT NULL DEFAULT 1",
                    "label": "TEXT",
                    "context": "TEXT",
                    "updated_at": "TEXT",
                    "owner_id": "TEXT",
                    "ranker_arm": "TEXT",
                    "deck_request_count": "INTEGER NOT NULL DEFAULT 0",
                },
                "swipes": {"consumed_version": "INTEGER"},
                "outfits": {"consumed_version": "INTEGER"},
                "corrections": {"consumed_version": "INTEGER"},
                "routes": {"owner_id": "TEXT"},
                "uploads": {"consumed_version": "INTEGER", "owner_id": "TEXT"},
            }
            if not database_url:
                for table, columns in migrations.items():
                    existing = {
                        row["name"]
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    for column, definition in columns.items():
                        if column not in existing:
                            connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                            )
                for table, definition in (
                    ("routes", "owner_id TEXT"),
                    ("uploads", "owner_id TEXT"),
                ):
                    existing = {
                        row["name"]
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    column = definition.split()[0]
                    if column not in existing:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
                upload_columns = {
                    row["name"]: row
                    for row in connection.execute("PRAGMA table_info(uploads)")
                }
                if upload_columns["profile_id"]["notnull"]:
                    connection.execute("ALTER TABLE uploads RENAME TO uploads_before_boards")
                    connection.execute(
                        """CREATE TABLE uploads (
                           id TEXT PRIMARY KEY, profile_id TEXT, image_path TEXT NOT NULL,
                           read_json TEXT, request_id TEXT, created_at TEXT NOT NULL,
                           consumed_version INTEGER, owner_id TEXT)"""
                    )
                    connection.execute(
                        """INSERT INTO uploads
                           SELECT id, profile_id, image_path, read_json, request_id,
                                  created_at, consumed_version, owner_id
                           FROM uploads_before_boards"""
                    )
                    connection.execute("DROP TABLE uploads_before_boards")
            connection.execute(
                """UPDATE profiles SET label=COALESCE(label, 'Your style'),
                   context=COALESCE(context, 'other'),
                   updated_at=COALESCE(updated_at, created_at)"""
            )
            connection.execute(
                """UPDATE profiles SET active=0
                   WHERE kind='user' AND active=1 AND id != (
                       SELECT candidate.id FROM profiles AS candidate
                       WHERE candidate.kind='user' AND candidate.active=1
                         AND (
                             candidate.owner_id=profiles.owner_id
                             OR (
                                 candidate.owner_id IS NULL
                                 AND profiles.owner_id IS NULL
                             )
                         )
                       ORDER BY COALESCE(candidate.updated_at, candidate.created_at) DESC,
                                candidate.created_at DESC, candidate.id DESC
                       LIMIT 1
                   )"""
            )
            if not database_url:
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(kind, active)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_routes_kind_corrected "
                    "ON routes(profile_kind, corrected, created_at)"
                )
        _INITIALIZED_DATABASES.add(database_key)


def insert_items(items: list[dict[str, Any]]) -> int:
    init_db()
    inserted = 0
    inserted_items = []
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
            if cursor.rowcount:
                inserted_items.append(row)
    if inserted_items and (
        os.getenv("QUENYA_CLIP_TEXT_PATH") or os.getenv("QUENYA_CLIP_TEXT_URL")
    ):
        from app.embedding import persist_item_text

        for item in inserted_items:
            try:
                persist_item_text(item)
            except Exception as exc:
                logger.error(
                    "Could not create text embedding for item %s (%s)",
                    item["id"], type(exc).__name__,
                )
    return inserted


def _maybe_embed_brief(profile_id: str, brief: dict[str, Any]) -> None:
    if not (os.getenv("QUENYA_CLIP_TEXT_PATH") or os.getenv("QUENYA_CLIP_TEXT_URL")):
        return
    from app.embedding import persist_profile_brief

    try:
        persist_profile_brief(profile_id, brief)
    except Exception as exc:
        logger.error(
            "Could not create text embedding for profile %s (%s)",
            profile_id, type(exc).__name__,
        )


def source_urls() -> set[str]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute("SELECT source_url FROM items").fetchall()
    return {row["source_url"] for row in rows}


def untagged_items(
    limit: int, source: str | None = None, retry_failed: bool = False
) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT * FROM items WHERE tags_json IS NULL"
    params: list[Any] = []
    if not retry_failed:
        query += " AND tag_error IS NULL"
    if source:
        query += " AND source=?"
        params.append(source)
    query += " ORDER BY fetched_at LIMIT ?"
    params.append(limit)
    with db_connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def save_tags(
    item_id: str,
    tags: dict[str, Any],
    request_id: str | None,
    cached_image_path: str | None = None,
    vectors: tuple[Any, Any, Any] | None = None,
) -> None:
    with db_connection() as connection:
        cursor = connection.execute(
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
        if vectors is not None and cursor.rowcount:
            from app.embedding import MODEL_NAME

            store_item_embedding(
                item_id, MODEL_NAME, *vectors, connection=connection
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
            """SELECT COUNT(*),
               SUM(CASE WHEN tags_json IS NOT NULL THEN 1 ELSE 0 END) FROM items"""
        ).fetchone()
    return int(total), int(tagged or 0)


def list_items(
    *,
    slot: str | None = None,
    gender: str | None = None,
    condition: str | None = None,
    source: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_db()
    clauses = [
        "tags_json IS NOT NULL",
        "(availability IS NULL OR (availability NOT LIKE '%OutOfStock' "
        "AND availability NOT LIKE '%SoldOut' "
        "AND availability NOT LIKE '%Discontinued'))",
    ]
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


_LIVE_CATALOGUE_COLUMNS = """id, source, source_url, title, brand, price_gbp, size,
    description, image_url, image_path, gender_hint, condition_hint,
    availability, query, fetched_at, tags_json, tags_request_id, tagged_at, tag_error"""
_LIVE_CATALOGUE_FILTER = """tags_json IS NOT NULL
    AND (availability IS NULL OR
         (availability NOT LIKE '%OutOfStock'
          AND availability NOT LIKE '%SoldOut'
          AND availability NOT LIKE '%Discontinued'))"""


def _load_live_catalogue() -> list[dict[str, Any]]:
    return _live_catalogue_items()


def _live_catalogue_items(query: str | None = None) -> list[dict[str, Any]]:
    sql = f"SELECT {_LIVE_CATALOGUE_COLUMNS} FROM items WHERE {_LIVE_CATALOGUE_FILTER}"
    params = ()
    if query is not None:
        sql += " AND query=?"
        params = (query,)
    sql += " ORDER BY fetched_at DESC"
    with db_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    catalogue = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item["tags_json"])
        catalogue.append(item)
    return catalogue


def profile_live_items(profile_id: str) -> list[dict[str, Any]]:
    init_db()
    return _live_catalogue_items(f"live:{profile_id}")


def _refresh_live_catalogue(generation: int) -> None:
    global _LIVE_CATALOGUE_CACHE, _LIVE_CATALOGUE_CACHE_EXPIRES_AT
    global _LIVE_CATALOGUE_REFRESHING
    try:
        catalogue = _load_live_catalogue()
    except Exception:
        logger.exception("Failed to refresh live catalogue")
    else:
        with _LIVE_CATALOGUE_LOCK:
            if generation == _LIVE_CATALOGUE_GENERATION:
                _LIVE_CATALOGUE_CACHE = catalogue
                _LIVE_CATALOGUE_CACHE_EXPIRES_AT = (
                    monotonic() + QUENYA_CATALOGUE_TTL_S
                )
    finally:
        with _LIVE_CATALOGUE_LOCK:
            _LIVE_CATALOGUE_REFRESHING = False


def _start_live_catalogue_refresh_locked() -> None:
    global _LIVE_CATALOGUE_REFRESHING
    _LIVE_CATALOGUE_REFRESHING = True
    try:
        Thread(
            target=_refresh_live_catalogue,
            args=(_LIVE_CATALOGUE_GENERATION,),
            daemon=True,
            name="live-catalogue-refresh",
        ).start()
    except Exception:
        _LIVE_CATALOGUE_REFRESHING = False
        logger.exception("Failed to start live catalogue refresh")


def invalidate_live_catalogue() -> None:
    global _LIVE_CATALOGUE_CACHE, _LIVE_CATALOGUE_CACHE_EXPIRES_AT
    global _LIVE_CATALOGUE_GENERATION
    with _LIVE_CATALOGUE_LOCK:
        _LIVE_CATALOGUE_CACHE = None
        _LIVE_CATALOGUE_CACHE_EXPIRES_AT = 0.0
        _LIVE_CATALOGUE_GENERATION += 1


def live_catalogue() -> list[dict[str, Any]]:
    global _LIVE_CATALOGUE_CACHE, _LIVE_CATALOGUE_CACHE_EXPIRES_AT
    init_db()
    with _LIVE_CATALOGUE_LOCK:
        if _LIVE_CATALOGUE_CACHE is None:
            catalogue = _load_live_catalogue()
            _LIVE_CATALOGUE_CACHE = catalogue
            _LIVE_CATALOGUE_CACHE_EXPIRES_AT = (
                monotonic() + QUENYA_CATALOGUE_TTL_S
            )
            return [dict(item) for item in catalogue]

        if (
            monotonic() >= _LIVE_CATALOGUE_CACHE_EXPIRES_AT
            and not _LIVE_CATALOGUE_REFRESHING
        ):
            _start_live_catalogue_refresh_locked()
        return [dict(item) for item in _LIVE_CATALOGUE_CACHE]


def items_for_embedding(limit: int = 100000) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM items ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


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
                "calls_toward_training": sum(call["ok"] for call in calls),
                "first_training_at": 25,
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


def create_profile(
    profile_id: str,
    brief: dict[str, Any],
    *,
    kind: str = "user",
    active: bool = True,
    label: str = "Your style",
    context: str = "other",
    owner_id: str | None = None,
    ranker_arm: str | None = None,
) -> None:
    init_db()
    if ranker_arm is None:
        from app.embedding_ranker import fallback_arm

        ranker_arm = fallback_arm(profile_id)
    with db_connection() as connection:
        if kind == "user" and active:
            connection.execute(
                "UPDATE profiles SET active=0 WHERE kind='user' AND active=1 "
                + ("AND owner_id=?" if owner_id is not None else ""),
                (owner_id,) if owner_id is not None else (),
            )
        connection.execute(
            """INSERT INTO profiles
               (id, brief_json, created_at, kind, active, owner_id, ranker_arm)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                json.dumps(brief, ensure_ascii=False, separators=(",", ":")),
                now_iso(),
                kind,
                int(active),
                owner_id,
                ranker_arm,
            ),
        )
        connection.execute(
            "UPDATE profiles SET label=?, context=?, updated_at=? WHERE id=?",
            (label, context, now_iso(), profile_id),
        )
    _maybe_embed_brief(profile_id, brief)


def update_profile_brief(profile_id: str, brief: dict[str, Any]) -> None:
    with db_connection() as connection:
        connection.execute(
            "UPDATE profiles SET brief_json=?, updated_at=? WHERE id=?",
            (json.dumps(brief, ensure_ascii=False, separators=(",", ":")), now_iso(), profile_id),
        )
    _maybe_embed_brief(profile_id, brief)


def _vector_string(vector) -> str | None:
    if vector is None:
        return None
    import numpy as np

    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    return "[" + ",".join(format(float(value), ".8g") for value in values) + "]"


def _vector_value(value):
    if value is None:
        return None
    import numpy as np

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return np.frombuffer(value, dtype=np.float32).copy()
    if isinstance(value, str):
        value = json.loads(value)
    return np.asarray(value, dtype=np.float32)


def store_item_embedding(
    item_id: str, model: str, img, txt, mix, connection=None
) -> None:
    if connection is None:
        init_db()
    if connection is not None:
        import sqlite3

        if isinstance(connection, _PostgresConnection):
            sql = """INSERT INTO item_embeddings(item_id, model, img, txt, mix, updated_at)
                     VALUES (?, ?, ?::vector, ?::vector, ?::vector, ?)
                     ON CONFLICT(item_id) DO UPDATE SET model=excluded.model,
                     img=excluded.img, txt=excluded.txt, mix=excluded.mix,
                     updated_at=excluded.updated_at"""
            values = (
                item_id, model, _vector_string(img), _vector_string(txt),
                _vector_string(mix), now_iso(),
            )
            connection.execute(sql, values)
        elif isinstance(connection, sqlite3.Connection):
            import numpy as np

            def blob(value):
                return None if value is None else np.asarray(value, dtype=np.float32).tobytes()

            connection.execute(
                """INSERT INTO item_embeddings(item_id, model, img, txt, mix, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(item_id) DO UPDATE SET model=excluded.model,
                   img=excluded.img, txt=excluded.txt, mix=excluded.mix,
                   updated_at=excluded.updated_at""",
                (item_id, model, blob(img), blob(txt), blob(mix), now_iso()),
            )
        else:
            sql = """INSERT INTO quenya.item_embeddings
                     (item_id, model, img, txt, mix, updated_at)
                     VALUES (%s, %s, %s::extensions.vector, %s::extensions.vector,
                             %s::extensions.vector, %s)
                     ON CONFLICT(item_id) DO UPDATE SET model=excluded.model,
                     img=excluded.img, txt=excluded.txt, mix=excluded.mix,
                     updated_at=excluded.updated_at"""
            connection.execute(
                sql,
                (
                    item_id, model, _vector_string(img), _vector_string(txt),
                    _vector_string(mix), now_iso(),
                ),
            )
        return
    if os.getenv("QUENYA_DATABASE_URL"):
        sql = """INSERT INTO item_embeddings(item_id, model, img, txt, mix, updated_at)
                 VALUES (?, ?, ?::vector, ?::vector, ?::vector, ?)
                 ON CONFLICT(item_id) DO UPDATE SET model=excluded.model,
                 img=excluded.img, txt=excluded.txt, mix=excluded.mix,
                 updated_at=excluded.updated_at"""
        values = (
            item_id, model, _vector_string(img), _vector_string(txt),
            _vector_string(mix), now_iso(),
        )
    else:
        import numpy as np

        def blob(value):
            return None if value is None else np.asarray(value, dtype=np.float32).tobytes()

        sql = """INSERT INTO item_embeddings(item_id, model, img, txt, mix, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(item_id) DO UPDATE SET model=excluded.model,
                 img=excluded.img, txt=excluded.txt, mix=excluded.mix,
                 updated_at=excluded.updated_at"""
        values = (item_id, model, blob(img), blob(txt), blob(mix), now_iso())
    with db_connection() as connection:
        connection.execute(sql, values)


def item_embedding_map() -> dict[str, dict[str, Any]]:
    init_db()
    postgres = bool(os.getenv("QUENYA_DATABASE_URL"))
    fields = "img::text, txt::text, mix::text" if postgres else "img, txt, mix"
    with db_connection() as connection:
        rows = connection.execute(
            f"SELECT item_id, {fields} FROM item_embeddings WHERE model='clip32'"
        ).fetchall()
    return {
        row["item_id"]: {
            key: _vector_value(row[key])
            for key in ("img", "txt", "mix")
        }
        for row in rows
    }


def item_mix_vectors(item_ids: list[str]) -> dict[str, dict[str, Any]]:
    init_db()
    item_ids = list(dict.fromkeys(item_ids))
    if not item_ids:
        return {}
    postgres = bool(os.getenv("QUENYA_DATABASE_URL"))
    vectors = {}
    with db_connection() as connection:
        if postgres:
            rows = connection.execute(
                """SELECT item_id, mix::text AS mix FROM item_embeddings
                   WHERE model='clip32' AND item_id = ANY(?) AND mix IS NOT NULL""",
                (item_ids,),
            ).fetchall()
            batches = [rows]
        else:
            batches = []
            for start in range(0, len(item_ids), 900):
                batch = item_ids[start : start + 900]
                placeholders = ",".join("?" for _ in batch)
                batches.append(connection.execute(
                    f"""SELECT item_id, mix FROM item_embeddings
                        WHERE model='clip32' AND item_id IN ({placeholders})
                        AND mix IS NOT NULL""",
                    tuple(batch),
                ).fetchall())
        for rows in batches:
            vectors.update({
                row["item_id"]: {"mix": _vector_value(row["mix"])}
                for row in rows
            })
    return vectors


def nearest_item_mix(
    query, eligible_ids: list[str], k: int = 400
) -> list[tuple[str, Any]]:
    init_db()
    eligible_ids = list(dict.fromkeys(eligible_ids))
    if not eligible_ids or k < 1:
        return []
    if os.getenv("QUENYA_DATABASE_URL"):
        with db_connection() as connection:
            rows = connection.execute(
                """SELECT item_id, mix::text AS mix FROM item_embeddings
                   WHERE model='clip32' AND item_id = ANY(?) AND mix IS NOT NULL
                   ORDER BY mix <#> ?::vector LIMIT ?""",
                (eligible_ids, _vector_string(query), k),
            ).fetchall()
        return [(row["item_id"], _vector_value(row["mix"])) for row in rows]

    import numpy as np

    vectors = item_mix_vectors(eligible_ids)
    if not vectors:
        return []
    ids = list(vectors)
    matrix = np.asarray([vectors[item_id]["mix"] for item_id in ids], dtype=np.float32)
    similarities = matrix @ np.asarray(query, dtype=np.float32)
    indexes = np.argsort(-similarities, kind="stable")[:k]
    return [(ids[index], matrix[index]) for index in indexes]


def random_item_mix_vectors(limit: int = 3000):
    init_db()
    order = "RANDOM()" if not os.getenv("QUENYA_DATABASE_URL") else "random()"
    with db_connection() as connection:
        rows = connection.execute(
            f"SELECT mix::text AS mix FROM item_embeddings WHERE model='clip32' "
            f"AND mix IS NOT NULL ORDER BY {order} LIMIT ?"
            if os.getenv("QUENYA_DATABASE_URL")
            else "SELECT mix FROM item_embeddings WHERE model='clip32' "
            "AND mix IS NOT NULL ORDER BY RANDOM() LIMIT ?",
            (limit,),
        ).fetchall()
    return [_vector_value(row["mix"]) for row in rows if row["mix"] is not None]


def store_profile_embedding(profile_id: str, model: str, brief, brief_hash: str) -> None:
    init_db()
    if os.getenv("QUENYA_DATABASE_URL"):
        sql = """INSERT INTO profile_embeddings(profile_id, model, brief, brief_hash, updated_at)
                 VALUES (?, ?, ?::vector, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET
                 model=excluded.model, brief=excluded.brief,
                 brief_hash=excluded.brief_hash, updated_at=excluded.updated_at"""
        values = (profile_id, model, _vector_string(brief), brief_hash, now_iso())
    else:
        import numpy as np

        value = None if brief is None else np.asarray(brief, dtype=np.float32).tobytes()
        sql = """INSERT INTO profile_embeddings(profile_id, model, brief, brief_hash, updated_at)
                 VALUES (?, ?, ?, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET
                 model=excluded.model, brief=excluded.brief,
                 brief_hash=excluded.brief_hash, updated_at=excluded.updated_at"""
        values = (profile_id, model, value, brief_hash, now_iso())
    with db_connection() as connection:
        connection.execute(sql, values)


def profile_embedding(profile_id: str):
    init_db()
    field = "brief::text AS brief" if os.getenv("QUENYA_DATABASE_URL") else "brief"
    with db_connection() as connection:
        row = connection.execute(
            f"SELECT {field} FROM profile_embeddings WHERE profile_id=? AND model='clip32'",
            (profile_id,),
        ).fetchone()
    return _vector_value(row["brief"]) if row else None


def get_or_assign_ranker_arm(profile_id: str, fallback: str) -> str:
    init_db()
    with db_connection() as connection:
        row = connection.execute(
            "SELECT ranker_arm FROM profiles WHERE id=?", (profile_id,)
        ).fetchone()
        arm = (row["ranker_arm"] if row else None) or fallback
        if row and not row["ranker_arm"]:
            connection.execute(
                "UPDATE profiles SET ranker_arm=? WHERE id=?", (arm, profile_id)
            )
    return arm


def next_deck_request(profile_id: str) -> int:
    init_db()
    with db_connection() as connection:
        connection.execute(
            "UPDATE profiles SET deck_request_count=deck_request_count+1 WHERE id=?",
            (profile_id,),
        )
        row = connection.execute(
            "SELECT deck_request_count FROM profiles WHERE id=?", (profile_id,)
        ).fetchone()
    return int(row["deck_request_count"]) if row else 0


def profile_swipes(profile_id: str) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT item_id, liked FROM swipes WHERE profile_id=? ORDER BY created_at, id",
            (profile_id,),
        ).fetchall()
    return [{"item_id": row["item_id"], "liked": bool(row["liked"])} for row in rows]


def recent_deck_item_ids(profile_id: str, limit: int = 5) -> list[str]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            """SELECT item_id FROM deck_events WHERE profile_id=?
               ORDER BY created_at DESC, position DESC LIMIT ?""",
            (profile_id, limit),
        ).fetchall()
    return [row["item_id"] for row in reversed(rows)]


def record_deck_events(events: list[dict[str, Any]]) -> None:
    if not events:
        return
    init_db()
    with db_connection() as connection:
        for event in events:
            connection.execute(
                """INSERT INTO deck_events
                   (id, profile_id, item_id, arm, policy, position, score, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(event[key] for key in (
                    "id", "profile_id", "item_id", "arm", "policy",
                    "position", "score", "created_at",
                )),
            )


def set_profile_label(profile_id: str, label: str) -> None:
    with db_connection() as connection:
        connection.execute(
            "UPDATE profiles SET label=?, updated_at=? WHERE id=?",
            (label, now_iso(), profile_id),
        )


def activate_profile(
    profile_id: str, *, kind: str = "user", owner_id: str | None = None
) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        query = "SELECT id FROM profiles WHERE id=? AND kind=?"
        params: list[Any] = [profile_id, kind]
        if kind == "user" and owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        row = connection.execute(query, params).fetchone()
        if not row:
            return None
        if kind == "user" and owner_id is not None:
            connection.execute(
                "UPDATE profiles SET active=0 WHERE kind=? AND owner_id=?",
                (kind, owner_id),
            )
        else:
            connection.execute("UPDATE profiles SET active=0 WHERE kind=?", (kind,))
        connection.execute(
            "UPDATE profiles SET active=1, updated_at=? WHERE id=?",
            (now_iso(), profile_id),
        )
    return get_profile(profile_id, kind=kind)


def profile_boards(kind: str = "user", owner_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        query = """SELECT * FROM profiles WHERE kind=?"""
        params: list[Any] = [kind]
        if kind == "user" and owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        query += " ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC"
        rows = connection.execute(query, params).fetchall()
    boards = []
    for row in rows:
        board = dict(row)
        board["brief"] = json.loads(board.pop("brief_json"))
        latest = latest_taste(board["id"])
        board["version"] = latest["version"] if latest else 0
        board["taste"] = latest["profile"] if latest else None
        boards.append(board)
    return boards


def create_route(
    profile_kind: str,
    input_kind: str,
    input_text: str,
    output: dict[str, Any],
    request_id: str | None,
    chosen_board_id: str | None,
    owner_id: str | None = None,
) -> str:
    route_id = uuid.uuid4().hex
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO routes
               (id, profile_kind, input_kind, input_text, output_json, request_id,
                chosen_board_id, created_at, owner_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                route_id, profile_kind, input_kind, input_text,
                json.dumps(output, ensure_ascii=False), request_id,
                chosen_board_id, now_iso(), owner_id,
            ),
        )
    return route_id


def get_route(
    route_id: str, *, kind: str = "user", owner_id: str | None = None
) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        query = "SELECT * FROM routes WHERE id=? AND profile_kind=?"
        params: list[Any] = [route_id, kind]
        if owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        row = connection.execute(query, params).fetchone()
    if not row:
        return None
    result = dict(row)
    result["output"] = json.loads(result.pop("output_json"))
    if result.get("corrected_to_json"):
        result["corrected_to"] = json.loads(result["corrected_to_json"])
    return result


def corrected_routes(
    kind: str = "user", limit: int = 8, owner_id: str | None = None
) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        query = """SELECT input_text, corrected_to_json FROM routes
                   WHERE profile_kind=? AND corrected=1"""
        params: list[Any] = [kind]
        if owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = connection.execute(
            query,
            params,
        ).fetchall()
    return [
        {
            "input_text": row["input_text"],
            "corrected_to": json.loads(row["corrected_to_json"] or "{}"),
        }
        for row in rows
    ]


def correct_route(
    route_id: str,
    correction: dict[str, Any],
    *,
    kind: str = "user",
    owner_id: str | None = None,
) -> None:
    with db_connection() as connection:
        query = (
            "UPDATE routes SET corrected=1, corrected_to_json=?, chosen_board_id=? "
            "WHERE id=? AND profile_kind=?"
        )
        params: list[Any] = [
            json.dumps(correction, ensure_ascii=False),
            correction.get("board_id"),
            route_id,
            kind,
        ]
        if owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        connection.execute(
            query,
            params,
        )


def assign_upload(upload_id: str, profile_id: str, owner_id: str | None = None) -> None:
    with db_connection() as connection:
        query = "UPDATE uploads SET profile_id=? WHERE id=?"
        params: list[Any] = [profile_id, upload_id]
        if owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        connection.execute(query, params)


def get_upload(upload_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        query = "SELECT * FROM uploads WHERE id=?"
        params: list[Any] = [upload_id]
        if owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        row = connection.execute(query, params).fetchone()
    if not row:
        return None
    result = dict(row)
    result["read"] = json.loads(result.pop("read_json") or "{}")
    return result


def route_for_upload(
    upload_id: str, kind: str = "user", owner_id: str | None = None
) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM routes WHERE profile_kind=? AND input_kind='photo'
               ORDER BY created_at DESC""",
            (kind,),
        ).fetchall()
    for row in rows:
        if owner_id is not None and row["owner_id"] != owner_id:
            continue
        output = json.loads(row["output_json"])
        if output.get("upload_id") == upload_id:
            result = dict(row)
            result["output"] = output
            return result
    return None


def remove_pending_brief_correction(profile_id: str, route_id: str) -> None:
    with db_connection() as connection:
        connection.execute(
            """DELETE FROM corrections WHERE profile_id=? AND kind='brief'
               AND target_id=? AND consumed_version IS NULL""",
            (profile_id, route_id),
        )


def merge_profile_data(source_id: str, target_id: str) -> None:
    with db_connection() as connection:
        for table in ("swipes", "outfits", "corrections", "uploads"):
            connection.execute(
                f"UPDATE {table} SET profile_id=? WHERE profile_id=?",
                (target_id, source_id),
            )
        connection.execute("DELETE FROM taste_versions WHERE profile_id=?", (source_id,))
        connection.execute("DELETE FROM profiles WHERE id=?", (source_id,))


def get_profile(profile_id: str, *, kind: str | None = None) -> dict[str, Any] | None:
    init_db()
    query = "SELECT * FROM profiles WHERE id=?"
    params: list[Any] = [profile_id]
    if kind:
        query += " AND kind=?"
        params.append(kind)
    with db_connection() as connection:
        row = connection.execute(query, params).fetchone()
    if not row:
        return None
    result = dict(row)
    result["brief"] = json.loads(result.pop("brief_json"))
    return result


def active_profile(kind: str = "user", owner_id: str | None = None) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        query = "SELECT * FROM profiles WHERE kind=? AND active=1"
        params: list[Any] = [kind]
        if kind == "user" and owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        query += " ORDER BY created_at DESC LIMIT 1"
        row = connection.execute(query, params).fetchone()
    if not row:
        return None
    result = dict(row)
    result["brief"] = json.loads(result.pop("brief_json"))
    return result


def latest_taste(profile_id: str) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        row = connection.execute(
            """SELECT * FROM taste_versions WHERE profile_id=?
               ORDER BY version DESC LIMIT 1""",
            (profile_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["profile"] = json.loads(result.pop("profile_json"))
    result["signals"] = json.loads(result.pop("signals_json"))
    return result


def get_taste_history(profile_id: str) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM taste_versions WHERE profile_id=?
               ORDER BY version DESC""",
            (profile_id,),
        ).fetchall()
    history = []
    for row in rows:
        item = dict(row)
        item["profile"] = json.loads(item.pop("profile_json"))
        item["signals"] = json.loads(item.pop("signals_json"))
        history.append(item)
    return history


def tag_evidence(profile_id: str) -> dict[str, dict[str, int]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            """SELECT s.liked, i.tags_json FROM swipes s
               LEFT JOIN items i ON i.id=s.item_id
               WHERE s.profile_id=?""",
            (profile_id,),
        ).fetchall()

    evidence: dict[str, dict[str, int]] = {}
    for row in rows:
        kind = "kept" if row["liked"] else "passed"
        for token in tokens({"tags_json": row["tags_json"]}):
            counts = evidence.setdefault(token, {"kept": 0, "passed": 0})
            counts[kind] += 1
    return evidence


def save_fetch_job(job_id: str, profile_id: str, state: dict[str, Any]) -> None:
    init_db()
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO fetch_jobs (id, profile_id, state_json, updated_at)
               VALUES (?, ?, ?, ?)""",
            (job_id, profile_id, json.dumps(state, ensure_ascii=False), now_iso()),
        )


def update_fetch_job(job_id: str, state: dict[str, Any]) -> None:
    init_db()
    with db_connection() as connection:
        connection.execute(
            "UPDATE fetch_jobs SET state_json=?, updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), now_iso(), job_id),
        )


def get_fetch_job(job_id_or_profile_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        query = (
            "SELECT f.state_json FROM fetch_jobs f "
            "LEFT JOIN profiles p ON p.id=f.profile_id WHERE f.id=?"
        )
        params: list[Any] = [job_id_or_profile_id]
        if owner_id is not None:
            query += " AND p.owner_id=?"
            params.append(owner_id)
        row = connection.execute(query, params).fetchone()
        if row:
            return json.loads(row["state_json"])
        query = (
            "SELECT f.state_json FROM fetch_jobs f "
            "LEFT JOIN profiles p ON p.id=f.profile_id WHERE f.profile_id=?"
        )
        params = [job_id_or_profile_id]
        if owner_id is not None:
            query += " AND p.owner_id=?"
            params.append(owner_id)
        rows = connection.execute(query, params).fetchall()
    jobs = [json.loads(row["state_json"]) for row in rows]
    return max(jobs, key=lambda job: job.get("started_at", ""), default=None)


def fetch_jobs(profile_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT state_json FROM fetch_jobs WHERE profile_id=? ORDER BY updated_at DESC",
            (profile_id,),
        ).fetchall()
    jobs = [json.loads(row["state_json"]) for row in rows]
    if kind is not None:
        jobs = [job for job in jobs if job.get("kind") == kind]
    return jobs


def unconsumed_signals(profile_id: str) -> dict[str, list[dict[str, Any]]]:
    init_db()
    with db_connection() as connection:
        swipe_rows = connection.execute(
            """SELECT s.*, i.title, i.price_gbp, i.tags_json
               FROM swipes s JOIN items i ON i.id=s.item_id
               WHERE s.profile_id=? AND s.consumed_version IS NULL
               ORDER BY s.created_at""",
            (profile_id,),
        ).fetchall()
        outfit_rows = connection.execute(
            """SELECT * FROM outfits WHERE profile_id=? AND verdict IS NOT NULL
               AND consumed_version IS NULL ORDER BY created_at""",
            (profile_id,),
        ).fetchall()
        correction_rows = connection.execute(
            """SELECT c.*, i.title FROM corrections c LEFT JOIN items i
               ON c.kind='tag' AND i.id=c.target_id
               WHERE c.profile_id=? AND c.kind IN ('tag', 'brief')
               AND c.consumed_version IS NULL ORDER BY c.created_at""",
            (profile_id,),
        ).fetchall()
        upload_rows = connection.execute(
            """SELECT * FROM uploads WHERE profile_id=? AND read_json IS NOT NULL
               AND consumed_version IS NULL ORDER BY created_at""",
            (profile_id,),
        ).fetchall()
    swipes = []
    for row in swipe_rows:
        item = dict(row)
        item["liked"] = bool(item["liked"])
        item["tags"] = json.loads(item.pop("tags_json") or "{}")
        swipes.append(item)
    return {
        "swipes": swipes,
        "outfits": [dict(row) for row in outfit_rows],
        "corrections": [dict(row) for row in correction_rows],
        "uploads": [
            {**dict(row), "read": json.loads(row["read_json"] or "{}")}
            for row in upload_rows
        ],
    }


def explicit_history_lines(profile_id: str) -> list[str]:
    init_db()
    with db_connection() as connection:
        outfit_rows = connection.execute(
            """SELECT * FROM outfits WHERE profile_id=? AND verdict IS NOT NULL
               ORDER BY created_at""",
            (profile_id,),
        ).fetchall()
        correction_rows = connection.execute(
            """SELECT c.*, i.title FROM corrections c LEFT JOIN items i
               ON c.kind='tag' AND i.id=c.target_id
               WHERE c.profile_id=? AND c.kind IN ('tag', 'brief')
               ORDER BY c.created_at""",
            (profile_id,),
        ).fetchall()
        upload_rows = connection.execute(
            """SELECT * FROM uploads WHERE profile_id=? AND read_json IS NOT NULL
               ORDER BY created_at""",
            (profile_id,),
        ).fetchall()

    lines = []
    for outfit in outfit_rows:
        pieces = [
            get_item_with_tags(item_id)
            for item_id in json.loads(outfit["piece_ids_json"])
        ]
        categories = [
            (item.get("tags") or {}).get("category", item["title"])
            for item in pieces
            if item
        ]
        action = "liked" if outfit["verdict"] == "like" else "rejected"
        line = f'- outfit {action} "{outfit["title"]}": {" + ".join(categories)}'
        if outfit["reason"]:
            line += f' | reason: "{outfit["reason"]}"'
        lines.append(line)
    for correction in correction_rows:
        if correction["kind"] == "brief":
            lines.append(f'- brief: "{correction["note"]}"')
        else:
            correction = dict(correction)
            title = correction.get("title") or correction["target_id"]
            lines.append(
                f'- tag correction on "{title}": "{correction["note"]}"'
            )
    for upload in upload_rows:
        read = json.loads(upload["read_json"] or "{}")
        lines.append(
            f'- inspiration photo: {read.get("one_line", "")} | '
            f'{", ".join(read.get("aesthetics", []))} | '
            f'palette {", ".join(read.get("palette", []))}'
        )
    return lines


def save_taste_version(
    profile_id: str,
    version: int,
    profile: dict[str, Any],
    signals: dict[str, Any],
    request_id: str | None,
) -> None:
    consumed = signals.get("consumed_ids", {})
    version_id = uuid.uuid4().hex
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO taste_versions
               (id, profile_id, version, profile_json, signals_json, request_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id,
                profile_id,
                version,
                json.dumps(profile, ensure_ascii=False),
                json.dumps(signals, ensure_ascii=False),
                request_id,
                now_iso(),
            ),
        )
        connection.execute(
            "UPDATE profiles SET updated_at=? WHERE id=?",
            (now_iso(), profile_id),
        )
        for table in ("swipes", "outfits", "corrections", "uploads"):
            ids = consumed.get(table, [])
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE {table} SET consumed_version=? WHERE id IN ({placeholders})",
                    [version, *ids],
                )


def insert_swipe(profile_id: str, item_id: str, liked: bool) -> None:
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO swipes (id, profile_id, item_id, liked, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, profile_id, item_id, int(liked), now_iso()),
        )
        connection.execute(
            "UPDATE profiles SET updated_at=? WHERE id=?", (now_iso(), profile_id)
        )


def swipe_history(profile_id: str) -> dict[str, Any]:
    init_db()
    with db_connection() as connection:
        rows = connection.execute(
            """SELECT s.liked, i.tags_json FROM swipes s
               LEFT JOIN items i ON i.id=s.item_id
               WHERE s.profile_id=?""",
            (profile_id,),
        ).fetchall()

    kept = sum(bool(row["liked"]) for row in rows)
    counts = {
        "aesthetics": Counter(),
        "categories": Counter(),
        "colours": Counter(),
    }
    for row in rows:
        if not row["liked"]:
            continue
        tags = tags_for({"tags_json": row["tags_json"]})
        category = tags.get("category")
        if (
            isinstance(category, str)
            and category.strip()
            and category.strip().lower() != "unknown"
        ):
            counts["categories"][category.strip()] += 1
        for key in ("aesthetics", "colours"):
            values = tags.get(key) or []
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, (list, tuple, set)):
                continue
            names = {
                value.strip()
                for value in values
                if isinstance(value, str)
                and value.strip()
                and value.strip().lower() != "unknown"
            }
            counts[key].update(names)

    return {
        "kept": kept,
        "passed": len(rows) - kept,
        **{
            key: sorted(counter.items(), key=lambda entry: (-entry[1], entry[0]))
            for key, counter in counts.items()
        },
    }


def save_outfit(
    profile_id: str,
    taste_version: int,
    outfit: dict[str, Any],
    piece_ids: list[str],
    total_gbp: float,
    request_id: str | None,
    outfit_id: str | None = None,
) -> str:
    outfit_id = outfit_id or uuid.uuid4().hex
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO outfits
               (id, profile_id, taste_version, title, mood, piece_ids_json,
                why, total_gbp, request_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                outfit_id,
                profile_id,
                taste_version,
                outfit["title"],
                outfit["mood"],
                json.dumps(piece_ids),
                outfit["why"],
                total_gbp,
                request_id,
                now_iso(),
            ),
        )
        connection.execute(
            "UPDATE profiles SET updated_at=? WHERE id=?", (now_iso(), profile_id)
        )
    return outfit_id


def get_outfit(outfit_id: str) -> dict[str, Any] | None:
    init_db()
    with db_connection() as connection:
        row = connection.execute("SELECT * FROM outfits WHERE id=?", (outfit_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["piece_ids"] = json.loads(result.pop("piece_ids_json"))
    return result


def update_outfit_pieces(outfit_id: str, piece_ids: list[str], total_gbp: float) -> None:
    with db_connection() as connection:
        connection.execute(
            "UPDATE outfits SET piece_ids_json=?, total_gbp=? WHERE id=?",
            (json.dumps(piece_ids), total_gbp, outfit_id),
        )


def set_outfit_verdict(outfit_id: str, verdict: str, reason: str | None) -> None:
    with db_connection() as connection:
        connection.execute(
            "UPDATE outfits SET verdict=?, reason=? WHERE id=?",
            (verdict, reason, outfit_id),
        )


def add_correction(
    profile_id: str,
    kind: str,
    target_id: str,
    before: Any,
    after: Any,
    note: str,
    request_id: str | None,
) -> str:
    correction_id = uuid.uuid4().hex
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO corrections
               (id, profile_id, kind, target_id, before_json, after_json, note,
                request_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                correction_id,
                profile_id,
                kind,
                target_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False) if after is not None else None,
                note,
                request_id,
                now_iso(),
            ),
        )
        connection.execute(
            "UPDATE profiles SET updated_at=? WHERE id=?", (now_iso(), profile_id)
        )
    return correction_id


def add_upload(
    profile_id: str | None, image_path: str, read: dict[str, Any],
    request_id: str | None, owner_id: str | None = None,
) -> str:
    upload_id = uuid.uuid4().hex
    with db_connection() as connection:
        connection.execute(
            """INSERT INTO uploads (id, profile_id, image_path, read_json,
            request_id, created_at, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                upload_id,
                profile_id,
                image_path,
                json.dumps(read, ensure_ascii=False),
                request_id,
                now_iso(), owner_id,
            ),
        )
        if profile_id:
            connection.execute(
                "UPDATE profiles SET updated_at=? WHERE id=?",
                (now_iso(), profile_id),
            )
    return upload_id


def signal_counts(profile_id: str) -> dict[str, int]:
    init_db()
    with db_connection() as connection:
        swipes, kept, passed = connection.execute(
            """SELECT COUNT(*),
               SUM(CASE WHEN liked=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN liked=0 THEN 1 ELSE 0 END)
               FROM swipes
               WHERE profile_id=?""",
            (profile_id,),
        ).fetchone()
        corrections = connection.execute(
            "SELECT COUNT(*) FROM corrections WHERE profile_id=?",
            (profile_id,),
        ).fetchone()[0]
        uploads = connection.execute(
            "SELECT COUNT(*) FROM uploads WHERE profile_id=?",
            (profile_id,),
        ).fetchone()[0]
    return {
        "swipes": int(swipes or 0),
        "kept": int(kept or 0),
        "passed": int(passed or 0),
        "corrections": int(corrections or 0),
        "uploads": int(uploads or 0),
    }


def get_item_with_tags(item_id: str) -> dict[str, Any] | None:
    item = get_item(item_id)
    if item and item.get("tags_json"):
        item["tags"] = json.loads(item["tags_json"])
    return item


def update_outfit_verdict(
    outfit_id: str, verdict: str, reason: str | None
) -> dict[str, Any] | None:
    with db_connection() as connection:
        row = connection.execute(
            "SELECT profile_id FROM outfits WHERE id=?", (outfit_id,)
        ).fetchone()
        connection.execute(
            "UPDATE outfits SET verdict=?, reason=? WHERE id=?",
            (verdict, reason, outfit_id),
        )
        row = connection.execute("SELECT * FROM outfits WHERE id=?", (outfit_id,)).fetchone()
        if row:
            connection.execute(
                "UPDATE profiles SET updated_at=? WHERE id=?",
                (now_iso(), row["profile_id"]),
            )
    if not row:
        return None
    result = dict(row)
    result["piece_ids"] = json.loads(result.pop("piece_ids_json"))
    return result
