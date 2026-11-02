"""Legal-hardening behaviour for Vinted photos and raw payloads."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from PIL import Image

from app import db, images, media_policy, phase2_api, tagging
from app.purge_vinted_images import run_purge
from app.sources.vinted import normalize_item
from app.vinted_retention import run_retention


def _jpeg_bytes(color=(20, 40, 60)) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def test_media_policy_defaults(monkeypatch):
    monkeypatch.delenv("QUENYA_PERSIST_RETAIL_IMAGES", raising=False)
    monkeypatch.delenv("QUENYA_LLM_TAG_VINTED_IMAGES", raising=False)
    assert media_policy.persist_listing_images("vinted") is False
    assert media_policy.hotlink_listing_images("vinted") is True
    assert media_policy.persist_listing_images("retail") is False
    assert media_policy.llm_may_receive_listing_photo("vinted") is False
    assert media_policy.llm_may_receive_listing_photo("retail") is True
    monkeypatch.setenv("QUENYA_PERSIST_RETAIL_IMAGES", "1")
    assert media_policy.persist_listing_images("retail") is True
    monkeypatch.setenv("QUENYA_LLM_TAG_VINTED_IMAGES", "1")
    assert media_policy.llm_may_receive_listing_photo("vinted") is True


def test_vinted_normalizer_omits_raw_json_and_seller_payload():
    item = {
        "id": 99,
        "title": "Canvas jacket",
        "price": 20,
        "url": "/items/99-canvas-jacket",
        "photos": [{"url": "https://images1.vinted.net/t/example.jpg"}],
        "user": {"id": 12345, "login": "seller_name"},
        "description": "Nice jacket",
        "brand_title": "Noah",
        "size_title": "M",
    }
    record = normalize_item(item, "canvas jacket", "m")
    assert record is not None
    assert record["raw_json"] is None
    assert record["brand"] == "Noah"
    assert record["size"] == "M"
    assert record["description"] == "Nice jacket"
    assert "seller_name" not in str(record)


def test_insert_items_stores_null_raw_json_for_vinted(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "privacy.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    record = {
        "id": "vinted:1",
        "source": "vinted",
        "source_url": "https://www.vinted.co.uk/items/1",
        "title": "Tee",
        "price_gbp": 10.0,
        "image_url": "https://images1.vinted.net/t/tee.jpg",
        "raw_json": None,
    }
    assert db.insert_items([record]) == 1
    stored = db.get_item("vinted:1")
    assert stored["raw_json"] in {None, "{}"}


def test_public_item_hotlinks_vinted_source_url():
    item = {
        "id": "vinted:2",
        "source": "vinted",
        "image_url": "https://images1.vinted.net/t/hotlink.jpg",
        "image_preview": "data:image/jpeg;base64,abc",
        "tags": {"slot": "top"},
    }
    public = phase2_api._public_item(item)
    assert public["image"] == "https://images1.vinted.net/t/hotlink.jpg"
    assert public["image_url"] == "https://images1.vinted.net/t/hotlink.jpg"
    assert public["image_preview"] is None
    assert "/img/" not in public["image"]


def test_public_item_uses_img_route_when_retail_persist_enabled(monkeypatch):
    monkeypatch.setenv("QUENYA_PERSIST_RETAIL_IMAGES", "1")
    item = {
        "id": "retail:1",
        "source": "retail",
        "image_url": "https://images.asos-media.com/products/x.jpg",
        "tags": {"slot": "top"},
    }
    public = phase2_api._public_item(item)
    assert public["image"].startswith("/img/")
    assert public["image"].endswith(".jpg")


def test_fetch_image_bytes_ephemeral_does_not_write_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(images, "IMAGE_DIR", tmp_path / "img")
    monkeypatch.setattr(images, "STORAGE_IMAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(images, "display_storage_enabled", lambda: False)
    monkeypatch.setattr(
        images, "download_display_image", lambda *_args, **_kwargs: _jpeg_bytes()
    )
    url = "https://images1.vinted.net/t/ephemeral.jpg"
    content = images.fetch_image_bytes(url, persist=False)
    assert content[:2] == b"\xff\xd8"
    assert not images.image_path(url).exists()


def test_image_input_for_vinted_does_not_persist(monkeypatch, tmp_path):
    monkeypatch.setattr(images, "IMAGE_DIR", tmp_path / "img")
    monkeypatch.setattr(images, "STORAGE_IMAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(images, "display_storage_enabled", lambda: False)
    monkeypatch.setattr(
        images, "download_display_image", lambda *_args, **_kwargs: _jpeg_bytes()
    )
    item = {
        "id": "vinted:3",
        "source": "vinted",
        "title": "Jacket",
        "image_url": "https://images1.vinted.net/t/input.jpg",
    }
    image = tagging._image_input(item)
    assert image is not None
    assert item.get("image_path") is None
    assert item.get("image_preview") is None
    assert not images.image_path(item["image_url"]).exists()


def test_llm_user_content_skips_vinted_photo_by_default(monkeypatch):
    called = {"persist": None}

    def fake_data_url(url, *, persist=True):
        called["persist"] = persist
        return "data:image/jpeg;base64,abc"

    monkeypatch.setattr(images, "image_data_url", fake_data_url)
    item = {
        "source": "vinted",
        "title": "Jacket",
        "brand": None,
        "price_gbp": 12,
        "size": "M",
        "description": None,
        "image_url": "https://images1.vinted.net/t/llm.jpg",
    }
    content = tagging.user_content(item)
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert called["persist"] is None


def test_llm_user_content_includes_vinted_photo_when_enabled(monkeypatch):
    monkeypatch.setenv("QUENYA_LLM_TAG_VINTED_IMAGES", "1")
    monkeypatch.setattr(
        images,
        "image_data_url",
        lambda url, *, persist=True: "data:image/jpeg;base64,abc",
    )
    item = {
        "source": "vinted",
        "title": "Jacket",
        "brand": None,
        "price_gbp": 12,
        "size": "M",
        "description": None,
        "image_url": "https://images1.vinted.net/t/llm.jpg",
    }
    content = tagging.user_content(item)
    assert len(content) == 2
    assert content[1]["type"] == "image_url"


def test_retention_deletes_old_and_sold_vinted_items(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "retention.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    recent = datetime.now(UTC).isoformat()
    db.insert_items(
        [
            {
                "id": "vinted:old",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/old",
                "title": "Old",
                "price_gbp": 10,
                "image_url": "https://images1.vinted.net/t/old.jpg",
                "fetched_at": old,
                "raw_json": None,
            },
            {
                "id": "vinted:sold",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/sold",
                "title": "Sold",
                "price_gbp": 10,
                "image_url": "https://images1.vinted.net/t/sold.jpg",
                "fetched_at": recent,
                "availability": "SoldOut",
                "raw_json": None,
            },
            {
                "id": "vinted:keep",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/keep",
                "title": "Keep",
                "price_gbp": 10,
                "image_url": "https://images1.vinted.net/t/keep.jpg",
                "fetched_at": recent,
                "raw_json": None,
            },
            {
                "id": "retail:1",
                "source": "retail",
                "source_url": "https://www.asos.com/prd/1",
                "title": "Retail",
                "price_gbp": 40,
                "image_url": "https://images.asos-media.com/products/1.jpg",
                "fetched_at": old,
                "raw_json": {"ok": True},
            },
        ]
    )
    result = run_retention(days=14, dry_run=False)
    assert result["deleted"] == 2
    assert db.get_item("vinted:old") is None
    assert db.get_item("vinted:sold") is None
    assert db.get_item("vinted:keep") is not None
    assert db.get_item("retail:1") is not None


def test_clear_vinted_raw_json(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "raw.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO items (
                   id, source, source_url, title, price_gbp, image_url,
                   fetched_at, raw_json
               ) VALUES (?, 'vinted', ?, 'Tee', 10, ?, ?, ?)""",
            (
                "vinted:raw",
                "https://www.vinted.co.uk/items/raw",
                "https://images1.vinted.net/t/raw.jpg",
                datetime.now(UTC).isoformat(),
                '{"user":{"login":"seller"}}',
            ),
        )
    cleared = db.clear_vinted_raw_json()
    assert cleared == 1
    stored = db.get_item("vinted:raw")["raw_json"]
    assert stored in {None, "{}"}


def test_clear_vinted_image_previews(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "preview.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO items (
                   id, source, source_url, title, price_gbp, image_url,
                   fetched_at, image_preview, raw_json
               ) VALUES (?, 'vinted', ?, 'Tee', 10, ?, ?, ?, NULL)""",
            (
                "vinted:preview",
                "https://www.vinted.co.uk/items/preview",
                "https://images1.vinted.net/t/preview.jpg",
                datetime.now(UTC).isoformat(),
                "data:image/jpeg;base64,abc",
            ),
        )
    assert db.clear_vinted_image_previews() == 1
    assert db.get_item("vinted:preview")["image_preview"] is None
    result = run_retention(days=14, dry_run=True, clear_previews=True)
    assert result["image_previews_cleared"] == 0


def test_purge_vinted_images_dry_run_default(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "purge.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    url = "https://images1.vinted.net/t/purge.jpg"
    db.insert_items(
        [
            {
                "id": "vinted:purge",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/purge",
                "title": "Purge",
                "price_gbp": 10,
                "image_url": url,
                "image_path": f"{images.image_key(url)}.jpg",
                "raw_json": None,
            }
        ]
    )
    deleted = {"count": 0}

    def fake_delete(path: str) -> None:
        deleted["count"] += 1

    monkeypatch.setattr(images, "delete_display_object", fake_delete)
    monkeypatch.setattr(images, "display_storage_enabled", lambda: True)
    dry = run_purge(execute=False)
    assert dry["dry_run"] is True
    assert dry["matched"] == 1
    assert deleted["count"] == 0
    executed = run_purge(execute=True)
    assert executed["deleted"] == 1
    assert deleted["count"] == 1


def test_image_headers_use_identifying_ua_for_vinted_cdn():
    headers = images._image_headers("https://images1.vinted.net/t/x.jpg")
    assert "QuenyaBot" in headers["User-Agent"]
    assert "quenya.co.uk" in headers["User-Agent"]
