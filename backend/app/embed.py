import argparse
import hashlib
import json
import os
import time
from io import BytesIO
from pathlib import Path

import numpy as np

from app import db
from app.embedding import MODEL_NAME, brief_text, encode_images, encode_texts, item_text

DEFAULT_EMBED_DIR = Path("/home/ubuntu/research/rec/emb/clip32")


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


def import_research(directory: Path) -> None:
    ids = json.loads((directory / "ids.json").read_text())
    images = np.load(directory / "img.npy")
    texts = np.load(directory / "txt.npy")
    for index, item_id in enumerate(ids):
        image = images[index]
        text = texts[index]
        mix = _unit(image + text) if np.linalg.norm(image) else _unit(text)
        db.store_item_embedding(
            item_id, MODEL_NAME,
            image if np.linalg.norm(image) else None, text, mix,
        )

    brief_ids_path = directory / "briefs_ids.json"
    brief_vectors_path = directory / "briefs.npy"
    if brief_ids_path.exists() and brief_vectors_path.exists():
        profile_ids = json.loads(brief_ids_path.read_text())
        brief_vectors = np.load(brief_vectors_path)
        profile_briefs = {}
        snapshot = directory.parents[1] / "snapshot" / "profiles.jsonl"
        if snapshot.exists():
            for line in snapshot.read_text().splitlines():
                row = json.loads(line)
                brief = row.get("brief_json") or {}
                if isinstance(brief, str):
                    brief = json.loads(brief)
                profile_briefs[row["id"]] = brief_text(brief)
        for profile_id, vector in zip(profile_ids, brief_vectors, strict=True):
            digest = hashlib.sha256(
                profile_briefs.get(profile_id, "").encode()
            ).hexdigest()
            db.store_profile_embedding(profile_id, MODEL_NAME, vector, digest)
    print(f"Imported {len(ids)} item vectors")


def _image_source(item: dict) -> str | None:
    image_path = item.get("image_path")
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    if image_path and base_url:
        from urllib.parse import quote

        return (
            f"{base_url}/storage/v1/object/public/quenya-img/"
            f"{quote(image_path, safe='/')}"
        )
    return item.get("image_url")


def backfill(batch_size: int) -> None:
    import httpx
    from PIL import Image

    items = db.items_for_embedding(limit=100000)
    current = db.item_embedding_map()
    started = time.monotonic()
    missing_text = [
        item for item in items
        if not current.get(item["id"]) or current[item["id"]].get("txt") is None
    ]
    for start in range(0, len(missing_text), batch_size):
        batch = missing_text[start : start + batch_size]
        vectors = encode_texts([item_text(item) for item in batch])
        with db.db_connection() as connection:
            for item, text in zip(batch, vectors, strict=True):
                old = current.get(item["id"]) or {}
                image = old.get("img")
                mix = _unit(image + text) if image is not None else text
                db.store_item_embedding(
                    item["id"], MODEL_NAME, image, text, mix, connection=connection
                )
                current[item["id"]] = {"img": image, "txt": text, "mix": mix}
        completed = min(start + len(batch), len(missing_text))
        elapsed = max(time.monotonic() - started, 1e-9)
        rate = completed / elapsed
        eta = (len(missing_text) - completed) / rate if rate else 0
        print(
            f"Text vectors {completed}/{len(missing_text)} "
            f"rate={rate:.2f}/s eta={eta:.0f}s",
            flush=True,
        )

    missing_images = [
        item for item in items
        if not current.get(item["id"]) or current[item["id"]].get("img") is None
    ]
    if missing_images:
        image_started = time.monotonic()
        checked = encoded = 0
        for start in range(0, len(missing_images), batch_size):
            batch = missing_images[start : start + batch_size]
            image_rows, image_items = [], []
            for item in batch:
                source = _image_source(item)
                if not source:
                    continue
                try:
                    response = httpx.get(source, follow_redirects=True, timeout=20)
                    response.raise_for_status()
                    with Image.open(BytesIO(response.content)) as opened:
                        image_rows.append(opened.convert("RGB"))
                    image_items.append(item)
                except Exception:
                    continue
            if image_rows:
                vectors = encode_images(image_rows)
                with db.db_connection() as connection:
                    for item, image in zip(image_items, vectors, strict=True):
                        old = current.get(item["id"]) or {}
                        text = old.get("txt")
                        mix = _unit(image + text) if text is not None else image
                        db.store_item_embedding(
                            item["id"], MODEL_NAME, image, text, mix,
                            connection=connection,
                        )
                        current[item["id"]] = {"img": image, "txt": text, "mix": mix}
                encoded += len(image_items)
            checked += len(batch)
            elapsed = max(time.monotonic() - image_started, 1e-9)
            rate = checked / elapsed
            eta = (len(missing_images) - checked) / rate if rate else 0
            print(
                f"Image vectors {checked}/{len(missing_images)} encoded={encoded} "
                f"rate={rate:.2f}/s eta={eta:.0f}s",
                flush=True,
            )

    with db.db_connection() as connection:
        rows = connection.execute("SELECT id, brief_json FROM profiles").fetchall()
    profiles = [
        {"id": row["id"], "brief": json.loads(row["brief_json"])}
        for row in rows
    ]
    missing_profiles = [
        (profile["id"], profile.get("brief") or {})
        for profile in profiles if db.profile_embedding(profile["id"]) is None
    ]
    for start in range(0, len(missing_profiles), batch_size):
        batch = missing_profiles[start : start + batch_size]
        texts = [brief_text(brief) for _profile_id, brief in batch]
        vectors = encode_texts(texts)
        for (profile_id, brief), vector in zip(batch, vectors, strict=True):
            text = brief_text(brief)
            db.store_profile_embedding(
                profile_id, MODEL_NAME, vector,
                hashlib.sha256(text.encode()).hexdigest(),
            )
    print(
        f"Backfill complete: {len(missing_text)} text, "
        f"{len(missing_images)} image, {len(missing_profiles)} profile vectors"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import-research")
    importer.add_argument("--dir", type=Path, default=DEFAULT_EMBED_DIR)
    importer.add_argument("--allow-postgres", action="store_true")
    filler = subparsers.add_parser("backfill")
    filler.add_argument("--batch-size", type=int, default=32)
    filler.add_argument("--allow-postgres", action="store_true")
    filler.add_argument("--skip-db-init", action="store_true")
    args = parser.parse_args()
    if getattr(args, "batch_size", 1) < 1:
        parser.error("--batch-size must be positive")
    if os.getenv("QUENYA_DATABASE_URL") and not args.allow_postgres:
        parser.error("set --allow-postgres to opt into a PostgreSQL backfill")
    if getattr(args, "skip_db_init", False):
        os.environ["QUENYA_SKIP_DB_INIT"] = "1"
    if args.command == "import-research":
        import_research(args.dir)
    else:
        backfill(args.batch_size)


if __name__ == "__main__":
    main()
