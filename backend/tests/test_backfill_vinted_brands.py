from app import db
from app.backfill_vinted_brands import backfill


def test_backfill_vinted_brands_updates_only_missing_brands(monkeypatch, tmp_path):
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "vinted-brands.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    db.insert_items(
        [
            {
                "id": "vinted:null",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/1",
                "title": "Stüssy top",
                "price_gbp": 3.75,
                "image_url": "https://images.example/1.jpg",
                "brand": None,
                "raw_json": {
                    "brand": None,
                    "brand_title": None,
                    "item_box": {
                        "accessibility_label": (
                            "Stüssy top, Brand: Stüssy, Condition: Good, Size: XS"
                        )
                    },
                },
            },
            {
                "id": "vinted:blank",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/2",
                "title": "Noah tee",
                "price_gbp": 12,
                "image_url": "https://images.example/2.jpg",
                "brand": "",
                "raw_json": {
                    "item_box": {
                        "accessibility_label": "Noah tee, Condition: Good",
                        "first_line": "Noah",
                    }
                },
            },
            {
                "id": "vinted:set",
                "source": "vinted",
                "source_url": "https://www.vinted.co.uk/items/3",
                "title": "Already tagged",
                "price_gbp": 12,
                "image_url": "https://images.example/3.jpg",
                "brand": "Already set",
                "raw_json": {
                    "item_box": {
                        "accessibility_label": "Item, Brand: Other, Condition: Good"
                    }
                },
            },
        ]
    )

    counts = backfill(batch_size=1)

    assert counts == {"scanned": 2, "found": 2, "updated": 2}
    with db.db_connection() as connection:
        rows = connection.execute(
            "SELECT id, brand FROM items WHERE source='vinted' ORDER BY id"
        ).fetchall()
    assert {row["id"]: row["brand"] for row in rows} == {
        "vinted:blank": "Noah",
        "vinted:null": "Stüssy",
        "vinted:set": "Already set",
    }
    db._INITIALIZED_DATABASES.clear()
