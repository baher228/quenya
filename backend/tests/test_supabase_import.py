import io
from types import SimpleNamespace

from PIL import Image

from app import db, images, supabase_import


def _record(index):
    return {
        "id": f"cos.com:{index}",
        "source": "cos.com",
        "source_url": f"https://www.cos.com/en-gb/product-{index}",
        "title": f"Wool jumper {index}",
        "brand": "COS",
        "price_gbp": 85,
        "image_url": f"https://images.example.test/{index}.jpg",
        "gender_hint": "m",
        "condition_hint": "new",
    }


def _image_bytes():
    image = Image.new("RGB", (48, 64), "navy")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def test_import_is_insert_only_and_tags_only_new_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(images, "IMAGE_DIR", tmp_path / "img")
    db.init_db()
    existing = _record("existing")
    existing["title"] = "Keep this existing title"
    assert db.insert_items([existing]) == 1

    remote_rows = []
    for index in ("one", "two", "existing"):
        record = _record(index)
        remote_rows.append({
            "url": record["source_url"],
            "source": record["source"],
            "title": record["title"],
            "brand": record["brand"],
            "price_gbp": record["price_gbp"],
            "gender": "m",
            "category": "knitwear",
            "condition": "new",
            "image_path": f"cos.com/{index}.jpg",
            "image_url_original": record["image_url"],
            "record": record,
        })
    imported = set()
    monkeypatch.setattr(
        supabase_import,
        "_pending_rows",
        lambda limit: [row for row in remote_rows if row["url"] not in imported][:limit],
    )
    monkeypatch.setattr(
        supabase_import,
        "_mark_imported",
        lambda urls: imported.update(urls),
    )
    monkeypatch.setattr(supabase_import, "_download_storage", lambda _path: _image_bytes())
    tagged = []

    def tagger(item):
        tagged.append(item["source_url"])
        return (
            SimpleNamespace(model_dump=lambda: {"category": "top"}),
            SimpleNamespace(request_id="stub-request"),
        )

    monkeypatch.setattr(supabase_import, "tag_item", tagger)

    first = supabase_import.run_import()
    assert first == {
        "fetched": 3, "inserted": 2, "tagged": 2, "skipped": 1,
        "tag_failed": 0, "failed": 0,
    }
    assert set(tagged) == {
        "https://www.cos.com/en-gb/product-one",
        "https://www.cos.com/en-gb/product-two",
    }
    assert images.image_path("https://images.example.test/one.jpg").is_file()
    assert images.image_path("https://images.example.test/two.jpg").is_file()

    second = supabase_import.run_import()
    assert second == {
        "fetched": 0, "inserted": 0, "tagged": 0, "skipped": 0,
        "tag_failed": 0, "failed": 0,
    }
    assert len(tagged) == 2
    original = db.get_item(existing["id"])
    assert original["title"] == "Keep this existing title"
    assert original["tags_json"] is None
