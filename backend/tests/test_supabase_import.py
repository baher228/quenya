import io
import json
import random
from types import SimpleNamespace

import httpx
import pytest
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


def _large_image_bytes():
    pixels = random.Random(17).randbytes(160 * 160 * 3)
    image = Image.frombytes("RGB", (160, 160), pixels)
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def _file_row(index, source="cos.com", gender="m"):
    record = _record(index)
    record["source"] = source
    record["source_url"] = f"https://www.{source}/products/{index}"
    record["id"] = f"{source}:{index}"
    record["gender_hint"] = gender
    return {
        "url": record["source_url"],
        "source": source,
        "title": record["title"],
        "brand": record["brand"],
        "price_gbp": record["price_gbp"],
        "gender": gender,
        "category": "knitwear",
        "condition": "new",
        "image_url_original": record["image_url"],
        "record": record,
    }


def _write_rows(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(images, "IMAGE_DIR", tmp_path / "img")
    db.init_db()


def _stub_tagger(monkeypatch):
    tagged = []

    def tagger(item):
        tagged.append(item["source_url"])
        return (
            SimpleNamespace(model_dump=lambda: {"category": "top"}),
            SimpleNamespace(request_id="stub-request"),
        )

    monkeypatch.setattr(supabase_import, "tag_item", tagger)
    return tagged


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


def test_file_import_tags_new_rows_and_rerun_is_noop(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    path = tmp_path / "listings.jsonl"
    rows = [_file_row("one"), _file_row("two")]
    _write_rows(path, rows)
    image_bytes = _large_image_bytes()
    calls = []

    class Response:
        content = image_bytes
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(supabase_import.httpx, "get", get)
    tagged = _stub_tagger(monkeypatch)

    first = supabase_import.run_file_import([path])
    assert first == {
        "fetched": 2, "inserted": 2, "tagged": 2, "skipped": 0,
        "tag_failed": 0, "failed": 0,
    }
    assert len(tagged) == 2
    assert all(kwargs["timeout"] == 15 for _, kwargs in calls)
    assert all("Mozilla" in kwargs["headers"]["User-Agent"] for _, kwargs in calls)
    for row in rows:
        item = db.get_item(row["record"]["id"])
        assert item["gender_hint"] == row["gender"]
        assert item["condition_hint"] == row["condition"]
        assert images.image_path(row["image_url_original"]).is_file()

    second = supabase_import.run_file_import([path])
    assert second == {
        "fetched": 2, "inserted": 0, "tagged": 0, "skipped": 2,
        "tag_failed": 0, "failed": 0,
    }
    assert len(tagged) == 2
    assert len(calls) == 2


def test_file_import_existing_urls_do_not_count_toward_per_shop(
    monkeypatch, tmp_path
):
    _isolated_db(monkeypatch, tmp_path)
    existing = _file_row("existing")
    assert db.insert_items([existing["record"]]) == 1
    path = tmp_path / "listings.jsonl"
    _write_rows(
        path,
        [
            existing,
            _file_row("one"),
            _file_row("two"),
            _file_row("three"),
        ],
    )

    image_bytes = _large_image_bytes()

    class Response:
        content = image_bytes

        def raise_for_status(self):
            return None

    monkeypatch.setattr(supabase_import.httpx, "get", lambda *_a, **_k: Response())

    first = supabase_import.run_file_import([path], per_shop=1, no_tag=True)
    assert first == {
        "fetched": 2, "inserted": 1, "tagged": 0, "skipped": 1,
        "tag_failed": 0, "failed": 0,
    }
    assert db.get_item("cos.com:one") is not None
    assert db.get_item("cos.com:two") is None

    second = supabase_import.run_file_import([path], per_shop=2, no_tag=True)
    assert second == {
        "fetched": 4, "inserted": 2, "tagged": 0, "skipped": 2,
        "tag_failed": 0, "failed": 0,
    }
    assert db.get_item("cos.com:two") is not None
    assert db.get_item("cos.com:three") is not None


def test_file_import_excludes_sources(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    path = tmp_path / "listings.jsonl"
    _write_rows(
        path,
        [_file_row("excluded", source="thenorthface.com"),
         _file_row("kept", source="cos.com")],
    )
    image_bytes = _large_image_bytes()

    class Response:
        content = image_bytes

        def raise_for_status(self):
            return None

    calls = []
    monkeypatch.setattr(
        supabase_import.httpx, "get",
        lambda url, **_kwargs: (calls.append(url) or Response()),
    )
    result = supabase_import.run_file_import(
        [path], exclude_sources=("thenorthface.com",), no_tag=True
    )
    assert result == {
        "fetched": 1, "inserted": 1, "tagged": 0, "skipped": 0,
        "tag_failed": 0, "failed": 0,
    }
    assert len(calls) == 1
    assert db.get_item("thenorthface.com:excluded") is None
    assert db.get_item("cos.com:kept") is not None


def test_file_import_counts_failed_image_without_inserting(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    path = tmp_path / "listings.jsonl"
    row = _file_row("failed")
    _write_rows(path, [row])
    monkeypatch.setattr(
        supabase_import.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    tagged = _stub_tagger(monkeypatch)

    result = supabase_import.run_file_import([path])
    assert result == {
        "fetched": 1, "inserted": 0, "tagged": 0, "skipped": 0,
        "tag_failed": 0, "failed": 1,
    }
    assert db.get_item(row["record"]["id"]) is None
    assert tagged == []


@pytest.mark.parametrize("content", [_image_bytes(), b"not-an-image" * 400])
def test_file_import_rejects_small_or_invalid_images(monkeypatch, tmp_path, content):
    _isolated_db(monkeypatch, tmp_path)
    path = tmp_path / "listings.jsonl"
    row = _file_row("invalid-image")
    _write_rows(path, [row])

    class Response:
        def raise_for_status(self):
            return None

        @property
        def content(self):
            return content

    monkeypatch.setattr(
        supabase_import.httpx, "get", lambda *_args, **_kwargs: Response()
    )
    result = supabase_import.run_file_import([path], no_tag=True)
    assert result == {
        "fetched": 1, "inserted": 0, "tagged": 0, "skipped": 0,
        "tag_failed": 0, "failed": 1,
    }
    assert db.get_item(row["record"]["id"]) is None
