import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.gender_rules import product_type_for_item, text_gender

BACKUP_DIR = Path("/home/ubuntu/research/gender")
READ_BATCH_SIZE = 500
UPDATE_BATCH_SIZE = 500


def _chunks(values: list[Any], size: int):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _raw_json_by_id(items: list[dict[str, Any]]) -> dict[str, Any]:
    item_ids = [item["id"] for item in items]
    raw_by_id = {}
    with db.db_connection() as connection:
        for batch in _chunks(item_ids, READ_BATCH_SIZE):
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"SELECT id, raw_json FROM items WHERE id IN ({placeholders})",
                batch,
            ).fetchall()
            raw_by_id.update({row["id"]: row["raw_json"] for row in rows})
    return raw_by_id


def _plan_changes(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter, dict]:
    raw_by_id = _raw_json_by_id(items)
    changes = Counter()
    by_source = defaultdict(Counter)
    updates = []
    for item in items:
        tags = item.get("tags")
        if not isinstance(tags, dict):
            continue
        gender = text_gender(
            item.get("title"),
            product_type_for_item({**item, "raw_json": raw_by_id.get(item["id"])}),
            item.get("source_url"),
        )
        old_gender = tags.get("gender")
        if gender is None or gender == old_gender:
            continue
        source = str(item.get("source") or "unknown")
        old_tags_json = item.get("tags_json")
        if old_tags_json is None:
            continue
        new_tags = {**tags, "gender": gender}
        changes[(old_gender, gender)] += 1
        by_source[source][(old_gender, gender)] += 1
        updates.append({
            "id": item["id"],
            "old_tags_json": old_tags_json,
            "new_tags_json": json.dumps(new_tags, ensure_ascii=False),
        })
    return updates, changes, by_source


def _write_backup(updates: list[dict[str, Any]]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = BACKUP_DIR / f"backup-{timestamp}.json"
    backup = {str(update["id"]): update["old_tags_json"] for update in updates}
    with path.open("x", encoding="utf-8") as file:
        json.dump(backup, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    path.chmod(0o600)
    return path


def _apply_updates(updates: list[dict[str, Any]]) -> tuple[int, int]:
    applied = conflicts = 0
    for batch in _chunks(updates, UPDATE_BATCH_SIZE):
        with db.db_connection() as connection:
            for update in batch:
                result = connection.execute(
                    """UPDATE items SET tags_json=?
                       WHERE id=? AND tags_json=?""",
                    (
                        update["new_tags_json"],
                        update["id"],
                        update["old_tags_json"],
                    ),
                )
                if result.rowcount:
                    applied += 1
                else:
                    conflicts += 1
    return applied, conflicts


def _safe_error(exc: Exception) -> str:
    message = db._redact_dsn(str(exc))
    database_url = os.getenv("QUENYA_DATABASE_URL")
    return message.replace(database_url, "[REDACTED DSN]") if database_url else message


def run(apply: bool = False) -> None:
    if not os.getenv("QUENYA_DATABASE_URL"):
        raise RuntimeError("QUENYA_DATABASE_URL is required")
    os.environ["QUENYA_SKIP_DB_INIT"] = "1"
    items = db.live_catalogue()
    updates, changes, by_source = _plan_changes(items)
    print(f"live_tagged_items={len(items)}")
    print(f"changes={changes}")
    print(f"change_count={len(updates)}")
    for source in sorted(by_source):
        print(f"{source}={by_source[source]}")
    if not apply:
        print("mode=dry-run")
        return
    if not updates:
        print("mode=apply; no changes; no backup written")
        return
    backup_path = _write_backup(updates)
    applied, conflicts = _apply_updates(updates)
    print(f"backup={backup_path}")
    print(f"applied={applied} conflicts={conflicts}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair gender tags from item text")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write corrected tags after saving the original tags_json backup",
    )
    args = parser.parse_args()
    try:
        run(apply=args.apply)
    except Exception as exc:
        print(f"{type(exc).__name__}: {_safe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
