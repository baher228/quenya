"""Preferred brands: schema cleaning, catalogue listing, ranking boost."""

from __future__ import annotations

from app import db
from app.rank import preferred_brand_boost, rank_items
from app.schemas import ProfileBrief


def _item(item_id: str, brand: str, *, slot: str = "top", price: float = 40) -> dict:
    return {
        "id": item_id,
        "title": f"{brand} piece",
        "brand": brand,
        "source": "vinted",
        "source_url": f"https://www.vinted.co.uk/items/{item_id}",
        "price_gbp": price,
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "tags": {
            "slot": slot,
            "category": "top",
            "colours": ["navy"],
            "materials": [],
            "fit": "regular",
            "era": "contemporary",
            "aesthetics": ["workwear"],
            "formality": 3,
            "niche": 0.5,
            "gender": "menswear",
            "logo_heavy": False,
            "condition": "second-hand",
            "one_line": "navy top",
        },
    }


def test_profile_brief_cleans_preferred_brands():
    brief = ProfileBrief(
        text="Clean Japanese workwear",
        looking_for="outfits",
        gender="menswear",
        condition="both",
        niche=0.5,
        budget_gbp=250,
        preferred_brands=["  Cos ", "cos", "Arket", "x" * 80, ""],
    )
    assert brief.preferred_brands == ["Cos", "Arket"]


def test_preferred_brand_boost_and_cold_start():
    item = _item("1", "COS")
    brief = {"preferred_brands": ["cos"]}
    assert preferred_brand_boost(item, brief, swipe_count=20) > 0
    cold = preferred_brand_boost(item, brief, swipe_count=0)
    warm = preferred_brand_boost(item, brief, swipe_count=20)
    assert cold > warm
    assert preferred_brand_boost(_item("2", "Other"), brief) == 0


def test_rank_items_boosts_preferred_brands_without_filtering():
    preferred = _item("pref", "Noah", price=55)
    other = _item("other", "Uniqlo", price=35)
    brief = {
        "text": "workwear",
        "looking_for": "outfits",
        "gender": "menswear",
        "condition": "both",
        "niche": 0.5,
        "budget_gbp": 300,
        "preferred_brands": ["Noah"],
    }
    profile = {
        "likes": {},
        "avoids": {},
        "rules": [],
        "niche_level": 0.5,
        "price_ceiling_gbp": 300,
        "condition_preference": "both",
    }
    ranked = rank_items(
        [other, preferred], brief, profile, swipe_count=0
    )
    assert [item["id"] for item in ranked] == ["pref", "other"]


def test_catalogue_brands_typeahead(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brands.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    now = "2026-01-01T00:00:00+00:00"
    with db.db_connection() as connection:
        for item_id, brand in (
            ("a", "COS"),
            ("b", "Arket"),
            ("c", "COS"),
            ("d", None),
        ):
            connection.execute(
                """INSERT INTO items (
                       id, source, source_url, title, brand, price_gbp, image_url,
                       fetched_at, tags_json, raw_json
                   ) VALUES (?, 'vinted', ?, ?, ?, 20, ?, ?, ?, NULL)""",
                (
                    item_id,
                    f"https://www.vinted.co.uk/items/{item_id}",
                    f"{brand or 'x'} tee",
                    brand,
                    f"https://images1.vinted.net/t/{item_id}.jpg",
                    now,
                    '{"slot":"top","condition":"second-hand","gender":"menswear"}',
                ),
            )
    assert db.catalogue_brands(limit=10) == ["Arket", "COS"]
    assert db.catalogue_brands(query="ar") == ["Arket"]
