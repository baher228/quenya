import argparse
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import httpx

from app import db, images

BATCH_SIZE = 500
STORAGE_LIST_PAGE_SIZE = 1000


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite_read_only(path: Path) -> sqlite3.Connection:
    uri_path = quote(str(path.resolve()), safe="/:")
    connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        )
    ]


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({_quote_identifier(table)})"
        )
    ]


def _copy_table(
    sqlite_connection: sqlite3.Connection,
    postgres_connection,
    table: str,
) -> int:
    columns = _table_columns(sqlite_connection, table)
    quoted_table = _quote_identifier(table)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    source_cursor = sqlite_connection.execute(
        f"SELECT {quoted_columns} FROM {quoted_table}"
    )
    row_count = 0
    while rows := source_cursor.fetchmany(BATCH_SIZE):
        row_values = [tuple(row) for row in rows]
        row_placeholders = ", ".join(
            "(" + ", ".join("?" for _ in columns) + ")" for _ in row_values
        )
        values = tuple(value for row in row_values for value in row)
        postgres_connection.execute(
            f"INSERT INTO {quoted_table} ({quoted_columns}) "
            f"VALUES {row_placeholders} ON CONFLICT DO NOTHING",
            values,
        )
        row_count += len(row_values)
    return row_count


def _postgres_count(connection, table: str) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
    ).fetchone()
    return int(row[0])


def migrate_database(sqlite_path: Path) -> list[tuple[str, int, int]]:
    if not os.getenv("QUENYA_DATABASE_URL"):
        raise RuntimeError("Set QUENYA_DATABASE_URL to the target PostgreSQL database")
    db.init_db()
    counts = []
    with _sqlite_read_only(sqlite_path) as source:
        source_tables = _table_names(source)
        tables = source_tables + (
            ["fetch_jobs"] if "fetch_jobs" not in source_tables else []
        )
        with db.db_connection() as destination:
            for table in tables:
                if table not in source_tables:
                    sqlite_count = 0
                else:
                    sqlite_count = _copy_table(source, destination, table)
                postgres_count = _postgres_count(destination, table)
                counts.append((table, sqlite_count, postgres_count))
            destination.execute(
                """SELECT setval(
                    pg_get_serial_sequence('llm_calls', 'id'),
                    COALESCE(MAX(id), 1),
                    MAX(id) IS NOT NULL
                ) FROM llm_calls"""
            )
    for table, sqlite_count, postgres_count in counts:
        print(
            f"{table}: SQLite {sqlite_count}, Postgres {postgres_count}"
        )
    return counts


def _storage_headers(secret: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {secret}",
        "apikey": secret,
    }


def _ensure_storage_bucket() -> tuple[str, str, dict[str, str]]:
    base_url, secret = images._storage_credentials()
    headers = _storage_headers(secret)
    bucket_url = f"{base_url}/storage/v1/bucket/{images.STORAGE_BUCKET}"
    response = httpx.get(bucket_url, headers=headers, timeout=15)
    if response.status_code == 404:
        response = httpx.post(
            f"{base_url}/storage/v1/bucket",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "id": images.STORAGE_BUCKET,
                "name": images.STORAGE_BUCKET,
                "public": True,
            },
            timeout=15,
        )
    response.raise_for_status()
    return base_url, secret, headers


def _existing_storage_objects(
    base_url: str,
    headers: dict[str, str],
    prefix: str,
) -> set[str]:
    existing = set()
    offset = 0
    while True:
        response = httpx.post(
            f"{base_url}/storage/v1/object/list/{images.STORAGE_BUCKET}",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "prefix": prefix.rstrip("/"),
                "limit": STORAGE_LIST_PAGE_SIZE,
                "offset": offset,
            },
            timeout=15,
        )
        response.raise_for_status()
        objects = response.json()
        if not isinstance(objects, list):
            raise RuntimeError("Unexpected response while listing Storage objects")
        for item in objects:
            name = item.get("name")
            if not name:
                continue
            if prefix and not name.startswith(prefix):
                name = f"{prefix}{name}"
            existing.add(name)
        offset += len(objects)
        if len(objects) < STORAGE_LIST_PAGE_SIZE:
            break
    return existing


def _upload_asset(path: Path, object_key: str) -> None:
    images.upload_storage_object(object_key, path.read_bytes(), "image/jpeg")


def _migrate_assets(directory: Path, prefix: str) -> tuple[int, int]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Asset directory not found: {directory}")
    base_url, _secret, headers = _ensure_storage_bucket()
    existing = _existing_storage_objects(base_url, headers, prefix)
    assets = []
    for path in sorted(directory.rglob("*.jpg")):
        relative = path.relative_to(directory).as_posix()
        object_key = f"{prefix}{relative}"
        if object_key in existing:
            continue
        assets.append((path, object_key))

    failures = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_upload_asset, path, object_key): object_key
            for path, object_key in assets
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                failures.append(f"{futures[future]}: {exc}")

    uploaded = len(assets) - len(failures)
    skipped = len(existing.intersection(
        f"{prefix}{path.relative_to(directory).as_posix()}"
        for path in directory.rglob("*.jpg")
    ))
    print(
        f"Storage {prefix or 'images'}: uploaded {uploaded}, skipped {skipped}, "
        f"failed {len(failures)}"
    )
    if failures:
        raise RuntimeError("; ".join(failures))
    return uploaded, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL")
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--uploads", type=Path)
    arguments = parser.parse_args()

    migrate_database(arguments.sqlite)
    if arguments.images or arguments.uploads:
        if arguments.images:
            _migrate_assets(arguments.images, "")
        if arguments.uploads:
            _migrate_assets(arguments.uploads, "uploads/")


if __name__ == "__main__":
    main()
