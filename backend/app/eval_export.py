"""Read-only, anonymised export of swipe history for offline replay evaluation.

Usage (production, never writes):
    python -m app.eval_export /tmp/quenya-eval.jsonl.gz

Style and owner ids are replaced by salted hashes (the salt is random per
export and never written), names/emails/invites/auth data are not read, and
the free-text brief is dropped unless --include-brief-text is given.
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import secrets
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from app.config import DB_PATH

EXPORT_VERSION = 1
_ITEM_COLUMNS = (
    "id, source, source_url, title, brand, price_gbp, size, image_url, "
    "gender_hint, condition_hint, availability, query, fetched_at, tags_json"
)
_LIVE_FILTER = """tags_json IS NOT NULL
    AND (availability IS NULL OR
         (availability NOT LIKE '%OutOfStock'
          AND availability NOT LIKE '%SoldOut'
          AND availability NOT LIKE '%Discontinued'))"""
_CHUNK = 500


class _ReadOnly:
    def __init__(self, database_url: str | None):
        self.postgres = bool(database_url)
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            self.connection = psycopg.connect(
                database_url, prepare_threshold=None, row_factory=dict_row
            )
            self.connection.read_only = True
            self.connection.execute("SET LOCAL search_path TO quenya, extensions")
        else:
            self.connection = sqlite3.connect(
                f"file:{DB_PATH}?mode=ro", uri=True, timeout=30
            )
            self.connection.row_factory = sqlite3.Row

    def rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        if self.postgres:
            sql = sql.replace("?", "%s")
        return [dict(row) for row in self.connection.execute(sql, tuple(params))]

    def rows_in(self, sql: str, ids: list[str]) -> list[dict[str, Any]]:
        result = []
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            if self.postgres:
                result.extend(self.rows(sql.replace("{ids}", "= ANY(?)"), (chunk,)))
            else:
                result.extend(
                    self.rows(
                        sql.replace("{ids}", "IN (SELECT value FROM json_each(?))"),
                        (json.dumps(chunk),),
                    )
                )
        return result

    def vector_field(self, column: str) -> str:
        return f"{column}::text AS {column}" if self.postgres else column

    def close(self) -> None:
        try:
            if self.postgres:
                self.connection.rollback()
        finally:
            self.connection.close()


def _vector(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return np.frombuffer(value, dtype=np.float32).copy()
    if isinstance(value, str):
        value = json.loads(value)
    return np.asarray(value, dtype=np.float32)


def encode_vector(vector: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vector, dtype=np.float16).tobytes()).decode()


def decode_vector(value: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(value), dtype=np.float16).astype(np.float32)


def _loads(value: Any) -> Any:
    if value is None or isinstance(value, dict | list):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def export(
    path: Path,
    *,
    include_brief_text: bool = False,
    vectors: str = "sample",
    sample_size: int = 3000,
    database_url: str | None = None,
    salt: str | None = None,
) -> dict[str, int]:
    if vectors not in {"sample", "all"}:
        raise ValueError("vectors must be 'sample' or 'all'")
    salt = salt or secrets.token_hex(16)

    def anon(value: Any) -> str | None:
        if value is None:
            return None
        return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:16]

    source = _ReadOnly(database_url)
    try:
        roles = {
            row["id"]: row["role"]
            for row in source.rows("SELECT id, role FROM users")
        }
        profiles = source.rows(
            "SELECT id, owner_id, kind, created_at, brief_json, ranker_arm FROM profiles"
        )
        swipes = source.rows(
            "SELECT profile_id, item_id, liked, created_at, id FROM swipes "
            "ORDER BY profile_id, created_at, id"
        )
        deck_events = source.rows(
            "SELECT profile_id, item_id, arm, policy, position, score, created_at "
            "FROM deck_events ORDER BY profile_id, created_at, position"
        )
        tastes = source.rows(
            "SELECT profile_id, version, profile_json, created_at FROM taste_versions "
            "ORDER BY profile_id, version"
        )
        outfits = source.rows(
            "SELECT profile_id, taste_version, piece_ids_json, verdict, created_at "
            "FROM outfits ORDER BY profile_id, created_at"
        )
        brief_vectors = source.rows(
            f"SELECT profile_id, {source.vector_field('brief')} FROM profile_embeddings "
            "WHERE model='clip32' AND brief IS NOT NULL"
        )
        live_items = source.rows(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE {_LIVE_FILTER}"
        )
        live_ids = {row["id"] for row in live_items}
        referenced = {row["item_id"] for row in swipes} | {
            row["item_id"] for row in deck_events
        }
        for row in outfits:
            referenced.update(_loads(row["piece_ids_json"]) or [])
        missing = sorted(referenced - live_ids)
        old_items = source.rows_in(
            f"SELECT {_ITEM_COLUMNS} FROM items WHERE id {{ids}}", missing
        )
        random_order = "random()" if source.postgres else "RANDOM()"
        sample_ids = [
            row["item_id"]
            for row in source.rows(
                "SELECT item_id FROM item_embeddings WHERE model='clip32' "
                f"AND mix IS NOT NULL ORDER BY {random_order} LIMIT ?",
                (sample_size,),
            )
        ]
        vector_ids = set(referenced) | set(sample_ids)
        if vectors == "all":
            vector_ids |= live_ids
        vector_rows = source.rows_in(
            f"SELECT item_id, {source.vector_field('mix')} FROM item_embeddings "
            "WHERE model='clip32' AND mix IS NOT NULL AND item_id {ids}",
            sorted(vector_ids),
        )
    finally:
        source.close()

    counts: dict[str, int] = {}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:

        def write(kind: str, record: dict[str, Any]) -> None:
            counts[kind] = counts.get(kind, 0) + 1
            handle.write(json.dumps({"kind": kind, **record}, ensure_ascii=False) + "\n")

        write("meta", {"version": EXPORT_VERSION, "vectors": vectors})
        for row in profiles:
            brief = _loads(row["brief_json"]) or {}
            if not include_brief_text:
                brief.pop("text", None)
            write("style", {
                "style": anon(row["id"]),
                "owner": anon(row["owner_id"]) or anon(f"no-owner:{row['id']}"),
                "role": roles.get(row["owner_id"], "unknown"),
                "profile_kind": row["kind"],
                "created_at": row["created_at"],
                "arm": row["ranker_arm"],
                "brief": brief,
            })
        for row in brief_vectors:
            vector = _vector(row["brief"])
            if vector is not None:
                write("brief_vector", {
                    "style": anon(row["profile_id"]), "v": encode_vector(vector)
                })
        for index, row in enumerate(swipes):
            write("swipe", {
                "style": anon(row["profile_id"]),
                "item_id": row["item_id"],
                "liked": bool(row["liked"]),
                "created_at": row["created_at"],
                "seq": index,
            })
        for row in deck_events:
            write("deck_event", {
                "style": anon(row["profile_id"]),
                "item_id": row["item_id"],
                "arm": row["arm"],
                "policy": row["policy"],
                "position": int(row["position"]),
                "score": float(row["score"]),
                "created_at": row["created_at"],
            })
        for row in tastes:
            write("taste", {
                "style": anon(row["profile_id"]),
                "version": int(row["version"]),
                "created_at": row["created_at"],
                "profile": _loads(row["profile_json"]) or {},
            })
        for row in outfits:
            write("outfit", {
                "style": anon(row["profile_id"]),
                "taste_version": row["taste_version"],
                "piece_ids": _loads(row["piece_ids_json"]) or [],
                "verdict": row["verdict"],
                "created_at": row["created_at"],
            })
        for row in [*live_items, *old_items]:
            query = row.get("query")
            if isinstance(query, str) and query.startswith("live:"):
                query = "live:" + anon(query.removeprefix("live:"))
            write("item", {
                **{key: row[key] for key in (
                    "id", "source", "source_url", "title", "brand", "price_gbp",
                    "size", "image_url", "gender_hint", "condition_hint",
                    "availability", "fetched_at",
                )},
                "query": query,
                "tags": _loads(row["tags_json"]) or {},
                "live": row["id"] in live_ids,
            })
        for row in vector_rows:
            vector = _vector(row["mix"])
            if vector is not None:
                write("vector", {"item_id": row["item_id"], "v": encode_vector(vector)})
        write("sample", {"item_ids": sample_ids})
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--include-brief-text", action="store_true")
    parser.add_argument("--vectors", choices=("sample", "all"), default="sample")
    parser.add_argument("--sample-size", type=int, default=3000)
    args = parser.parse_args()
    counts = export(
        args.output,
        include_brief_text=args.include_brief_text,
        vectors=args.vectors,
        sample_size=args.sample_size,
        database_url=os.getenv("QUENYA_DATABASE_URL"),
    )
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
