import pytest

from app import db
from app.backfill_sizes import backfill
from app.sizes import fits_brief, listing_size_from_label
from app.sources.vinted import normalize_item


def _item(
    slot: str,
    size: str | None = None,
    *,
    title: str = "Second-hand item",
    source: str = "vinted",
    condition: str = "second-hand",
) -> dict:
    return {
        "source": source,
        "condition_hint": condition,
        "size": size,
        "title": title,
        "tags": {"slot": slot},
    }


def test_listing_size_from_accessibility_label():
    assert listing_size_from_label(
        "Band Tshirt, Brand: New Look, Condition: Very good, "
        "Size: M / UK 12-14, 1.50 £, 2.28 £"
    ) == "M / UK 12-14"
    assert listing_size_from_label("Band Tshirt, no size available") is None
    assert listing_size_from_label("Size: XL") == "XL"


@pytest.mark.parametrize(
    ("listing_size", "expected"),
    [
        ("M", True),
        ("L", True),
        ("XL", True),
        ("M / UK 12-14", True),
        ("S", False),
        ("XS", False),
        ("UK 8-10", False),
    ],
)
def test_tops_match_within_one_letter_step(listing_size, expected):
    assert fits_brief(_item("top", listing_size), {"tops": "L"}) is expected


@pytest.mark.parametrize(
    ("listing_size", "expected"),
    [
        ("W32", True),
        ("W33", True),
        ("32/30", True),
        ("32x30", True),
        ("waist 32", True),
        ("32", True),
        ("W36", False),
    ],
)
def test_waist_sizes_match_within_one_inch(listing_size, expected):
    assert fits_brief(_item("bottom", listing_size), {"waist": "32"}) is expected


def test_letter_sized_bottom_uses_tops_size():
    assert fits_brief(
        _item("bottom", "M"), {"waist": "32", "tops": "L"}
    )


@pytest.mark.parametrize(
    ("listing_size", "expected"),
    [("11", True), ("EU 45", True), ("7", False)],
)
def test_shoe_sizes_match_within_half_a_size(listing_size, expected):
    assert fits_brief(_item("shoes", listing_size), {"shoes": "11"}) is expected


def test_non_vinted_and_unparseable_or_blank_sizes_pass():
    assert fits_brief(
        _item("top", "XS", source="arket.com", condition="new"),
        {"tops": "L"},
    )
    assert fits_brief(_item("top", "mystery"), {"tops": "L"})
    assert fits_brief(_item("top", "S"), {"tops": ""})
    assert fits_brief(_item("top", "one size"), {"tops": "L"})
    assert fits_brief(_item("top", "OS"), {"tops": "L"})
    assert fits_brief(_item("bag", "XS"), {"tops": "L"})


@pytest.mark.parametrize(
    ("slot", "title", "sizes", "expected"),
    [
        ("top", "Vintage tee size xs", {"tops": "L"}, False),
        ("top", "Vintage tee (Large)", {"tops": "M"}, True),
        ("bottom", "Vintage trousers W36 L29", {"waist": "32"}, False),
        ("bottom", "Vintage jeans 30/30", {"waist": "31"}, True),
        ("shoes", "Runner size 7", {"shoes": "11"}, False),
        ("shoes", "Vintage shoe sz 10", {"shoes": "10.5"}, True),
    ],
)
def test_title_size_fallback(slot, title, sizes, expected):
    assert fits_brief(_item(slot, title=title), sizes) is expected


def test_vinted_normalizer_uses_item_box_accessibility_size():
    item = {
        "id": 123,
        "title": "Band Tshirt",
        "price": 1.5,
        "url": "/items/123-band-tshirt",
        "photos": [{"url": "https://images.example/item.jpg"}],
        "size_title": None,
        "item_box": {
            "accessibility_label": (
                "Band Tshirt, Brand: New Look, Size: M / UK 12-14, 1.50 £"
            )
        },
    }

    normalized = normalize_item(item, "band tee", "w")

    assert normalized is not None
    assert normalized["size"] == "M / UK 12-14"


def test_backfill_sizes_dry_run_uses_fixture_database(monkeypatch, tmp_path):
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sizes.sqlite")
    items = [
        {
            "id": "vinted:1",
            "source": "vinted",
            "source_url": "https://www.vinted.co.uk/items/1",
            "title": "Tshirt",
            "price_gbp": 1.5,
            "image_url": "https://images.example/1.jpg",
            "raw_json": {
                "item_box": {
                    "accessibility_label": "Tshirt, Size: M / UK 12-14, 1.50 £"
                }
            },
        },
        {
            "id": "vinted:2",
            "source": "vinted",
            "source_url": "https://www.vinted.co.uk/items/2",
            "title": "Tshirt",
            "price_gbp": 1.5,
            "image_url": "https://images.example/2.jpg",
            "raw_json": {"item_box": {"accessibility_label": "Tshirt, no size"}},
        },
    ]
    db.insert_items(items)

    counts = backfill(dry_run=True, batch_size=1)

    assert counts == {"scanned": 2, "found": 1, "updated": 0}
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT size FROM items WHERE id='vinted:1'"
        ).fetchone()
    assert row["size"] is None
