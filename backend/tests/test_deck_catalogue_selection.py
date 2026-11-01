import json
from itertools import zip_longest

import pytest

from app import db, phase2_api
from app.accessories import is_accessory_slot
from app.rank import _is_secondhand, rank_items, tags_for


def _legacy_candidates(items):
    clothing = [item for item in items if not is_accessory_slot(tags_for(item).get("slot"))]
    extras = [item for item in items if is_accessory_slot(tags_for(item).get("slot"))]
    secondhand = [item for item in clothing if _is_secondhand(item)][:2000]
    new = [item for item in clothing if not _is_secondhand(item)][:5000 - len(secondhand)]
    return secondhand + new + extras[:500]


def _item(item_id, slot, condition="new", source="shop"):
    return {
        "id": item_id, "source": source, "price_gbp": 20,
        "title": "Cotton shirt", "fetched_at": "2026-01-01",
        "source_url": f"https://example.test/{item_id}",
        "tags": {
            "slot": slot, "condition": condition, "gender": "unisex",
            "colours": ["navy"], "materials": ["cotton"], "category": "shirt",
        },
    }


@pytest.mark.parametrize("new,secondhand,extras", [
    (0, 0, 0), (6000, 0, 0), (0, 3000, 0), (0, 0, 700),
    (6000, 100, 700), (6000, 3000, 700), (1, 1, 1),
])
def test_bounded_candidate_pass_preserves_legacy_items_and_order(new, secondhand, extras):
    buckets = [
        [_item(f"new-{index}", "top") for index in range(new)],
        [_item(f"used-{index}", "bottom", "second-hand") for index in range(secondhand)],
        [_item(f"extra-{index}", "bag" if index % 2 else "accessory") for index in range(extras)],
    ]
    items = [item for group in zip_longest(*buckets) for item in group if item is not None]
    expected = _legacy_candidates(items)
    actual = phase2_api._deck_candidates(items)
    assert actual == expected
    assert all(left is right for left, right in zip(actual, expected, strict=True))
    assert len(actual) <= 5500


def test_candidate_condition_fallback_and_raw_tag_json_stay_unchanged():
    items = []
    for source in ("shop", "vinted"):
        for condition in (None, "", False, "new", "second-hand", "used"):
            item = _item(f"{source}-{condition}", "top", condition, source)
            item["tags_json"] = json.dumps(item.pop("tags"))
            items.append(item)
    missing_condition = _item("missing", "top", source="vinted")
    missing_condition["tags"].pop("condition")
    items.append(missing_condition)
    assert phase2_api._deck_candidates(items) == _legacy_candidates(items)


def test_candidate_pass_stops_only_after_all_buckets_are_filled():
    items = [
        *[_item(f"used-{index}", "top", "second-hand") for index in range(2000)],
        *[_item(f"new-{index}", "top") for index in range(3000)],
        *[_item(f"extra-{index}", "bag") for index in range(500)],
    ]

    class NoTailRead(list):
        def __iter__(self):
            yield from super().__iter__()
            raise AssertionError("Selection must stop after all buckets are full")

    assert phase2_api._deck_candidates(NoTailRead(items)) == items


def test_selected_catalogue_copies_only_selected_rows_and_preserves_ranking(monkeypatch):
    items = [_item(f"item-{index}", "top") for index in range(6000)]
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "_load_live_catalogue", lambda: items)
    expected = _legacy_candidates(db.live_catalogue())
    saw_cached_rows = []

    def select(snapshot):
        saw_cached_rows.append(snapshot is items)
        return phase2_api._deck_candidates(snapshot)

    selected = db.live_catalogue(select=select)
    assert saw_cached_rows == [True]
    assert selected == expected
    assert len(selected) == 5000
    assert all(left is not right for left, right in zip(selected, items, strict=False))
    selected[0]["title"] = "Modified caller copy"
    assert items[0]["title"] == "Cotton shirt"
    selected[0]["title"] = items[0]["title"]
    brief = {"gender": "menswear", "condition": "both", "budget_gbp": 100}
    profile = {"likes": {"colour:navy": 0.5}, "avoids": {}}
    assert rank_items(selected, brief, profile) == rank_items(expected, brief, profile)
    assert len(db.live_catalogue()) == 6000


def test_selected_catalogue_uses_new_snapshot_after_invalidation(monkeypatch):
    monkeypatch.setattr(db, "init_db", lambda: None)
    snapshot = [_item("old", "top")]
    monkeypatch.setattr(db, "_load_live_catalogue", lambda: list(snapshot))
    assert db.live_catalogue(select=phase2_api._deck_candidates)[0]["id"] == "old"
    snapshot[:] = [_item("new", "bottom")]
    assert db.live_catalogue(select=phase2_api._deck_candidates)[0]["id"] == "old"
    db.invalidate_live_catalogue()
    assert db.live_catalogue(select=phase2_api._deck_candidates)[0]["id"] == "new"
