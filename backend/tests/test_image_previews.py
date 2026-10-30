import base64
import io
import sqlite3

from PIL import Image

from app import db, images, phase2_api, tagging
from app.backfill_previews import backfill


def _image_bytes(size: tuple[int, int] = (640, 480)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format="PNG")
    return buffer.getvalue()


def _record(item_id: str, image_url: str) -> dict:
    return {
        "id": item_id,
        "source": "retail",
        "source_url": f"https://shop.example/{item_id}",
        "title": "Navy jacket",
        "price_gbp": 80,
        "image_url": image_url,
        "raw_json": {},
    }


def test_preview_data_url_is_a_small_24px_jpeg():
    preview = images.preview_data_url(_image_bytes((600, 300)))
    assert preview.startswith("data:image/jpeg;base64,")
    decoded = base64.b64decode(preview.partition(",")[2])
    with Image.open(io.BytesIO(decoded)) as image:
        assert max(image.size) <= 24
        assert image.format == "JPEG"
    assert len(preview) < 1500


def test_public_item_includes_preview_without_raw_json():
    preview = "data:image/jpeg;base64,preview"
    public = phase2_api._public_item(
        {"id": "item", "image_url": None, "image_preview": preview, "raw_json": "private"}
    )
    assert public["image_preview"] == preview
    assert "raw_json" not in public


def test_sqlite_migration_adds_preview_to_existing_items_table(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.sqlite")
    db._INITIALIZED_DATABASES.clear()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.executescript(db.SCHEMA.replace("    image_preview TEXT,\n", ""))

    db.init_db()
    with db.db_connection() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(items)")
        }
    assert "image_preview" in columns
    db._INITIALIZED_DATABASES.clear()


def test_tagging_ingest_persists_preview_without_redownloading(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ingest.sqlite")
    monkeypatch.setattr(images, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(images, "display_storage_enabled", lambda: False)
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    record = _record("ingested", "https://images.example/item.jpg")
    cached_path = images.image_path(record["image_url"])
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_bytes(_image_bytes())
    db.insert_items([record])

    tagging.user_content(record)
    db.save_tags(
        record["id"], {"slot": "outerwear"}, None,
        image_preview=record["image_preview"],
    )
    db.save_tags(record["id"], {"slot": "outerwear"}, None, image_preview=None)

    preview = db.get_item(record["id"])["image_preview"]
    assert preview.startswith("data:image/jpeg;base64,")
    assert db.live_catalogue()[0]["image_preview"] == preview
    db._INITIALIZED_DATABASES.clear()


def test_backfill_dry_run_and_real_run_skip_failures(monkeypatch, tmp_path):
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "backfill.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    records = [
        _record("a-item", "https://images.example/a.jpg"),
        _record("b-item", "https://images.example/b.jpg"),
        _record("c-item", "https://images.example/c.jpg"),
    ]
    db.insert_items(records)
    content = _image_bytes()

    def download(path: str) -> bytes:
        if path == f"{images.image_key(records[1]['image_url'])}.jpg":
            raise OSError("missing image")
        return content

    monkeypatch.setattr(images, "display_storage_enabled", lambda: True)
    monkeypatch.setattr(images, "download_display_object", download)
    dry_run = backfill(dry_run=True, batch_size=1)
    assert dry_run == {"scanned": 3, "built": 2, "failed": 1, "updated": 0}
    assert all(db.get_item(record["id"])["image_preview"] is None for record in records)

    result = backfill(batch_size=2, limit=3)
    assert result == {"scanned": 3, "built": 2, "failed": 1, "updated": 2}
    assert db.get_item("a-item")["image_preview"].startswith("data:image/jpeg;base64,")
    assert db.get_item("b-item")["image_preview"] is None
    assert db.get_item("c-item")["image_preview"].startswith("data:image/jpeg;base64,")
    db._INITIALIZED_DATABASES.clear()
