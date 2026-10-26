from contextlib import contextmanager

from app import db, phase2_api


def _item(item_id: str, availability: str | None = "InStock") -> dict:
    return {
        "id": item_id,
        "source": "test",
        "source_url": f"https://example.test/{item_id}",
        "title": item_id,
        "price_gbp": 20.0,
        "image_url": f"https://example.test/{item_id}.jpg",
        "availability": availability,
    }


def _tag(item_id: str) -> None:
    db.save_tags(
        item_id,
        {
            "slot": "top",
            "category": "shirt",
            "colours": ["blue"],
            "materials": ["cotton"],
            "fit": "regular",
            "era": "contemporary",
            "aesthetics": ["casual"],
            "formality": 2,
            "niche": 0.2,
            "gender": "unisex",
            "logo_heavy": False,
            "condition": "new",
            "one_line": "A blue cotton shirt.",
        },
        None,
    )


def test_live_catalogue_filters_and_returns_all_eligible_items(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "catalogue.sqlite")
    monkeypatch.delenv("QUENYA_CLIP_TEXT_PATH", raising=False)
    monkeypatch.delenv("QUENYA_CLIP_TEXT_URL", raising=False)
    eligible = [f"eligible-{index}" for index in range(6)]
    records = [_item(item_id) for item_id in eligible]
    records.extend(
        [
            _item("out-of-stock", "https://schema.org/OutOfStock"),
            _item("sold-out", "https://schema.org/SoldOut"),
            _item("discontinued", "https://schema.org/Discontinued"),
            _item("untagged"),
        ]
    )
    assert db.insert_items(records) == len(records)
    for item_id in [*eligible, "out-of-stock", "sold-out", "discontinued"]:
        _tag(item_id)

    catalogue = db.live_catalogue()

    assert {item["id"] for item in catalogue} == set(eligible)
    assert all("raw_json" not in item for item in catalogue)
    assert all(isinstance(item["tags"], dict) for item in catalogue)


def test_live_catalogue_cache_ttl_invalidation_and_copy(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cache.sqlite")
    monkeypatch.delenv("QUENYA_CLIP_TEXT_PATH", raising=False)
    monkeypatch.delenv("QUENYA_CLIP_TEXT_URL", raising=False)
    monkeypatch.setattr(db, "QUENYA_CATALOGUE_TTL_S", 5)
    clock = [10.0]
    monkeypatch.setattr(db, "monotonic", lambda: clock[0])
    assert db.insert_items([_item("cached")]) == 1
    _tag("cached")

    original_db_connection = db.db_connection
    calls = 0

    @contextmanager
    def counted_db_connection():
        nonlocal calls
        calls += 1
        with original_db_connection() as connection:
            yield connection

    monkeypatch.setattr(db, "db_connection", counted_db_connection)
    first = db.live_catalogue()
    assert calls == 1
    first[0]["title"] = "mutated copy"
    assert db.live_catalogue()[0]["title"] == "cached"
    assert calls == 1

    clock[0] = 15.0
    assert db.live_catalogue()[0]["title"] == "cached"
    assert calls == 2

    db.invalidate_live_catalogue()
    assert db.live_catalogue()[0]["title"] == "cached"
    assert calls == 3


def test_public_item_does_not_include_raw_json():
    item = {
        **_item("public"),
        "raw_json": "{\"large\":\"payload\"}",
        "tags_json": "{\"slot\":\"top\"}",
    }

    result = phase2_api._public_item(item)

    assert "raw_json" not in result
