import json
import os
from contextlib import contextmanager
from itertools import zip_longest

import pytest

from app import db, phase2_api
from app.rank import rank_items


@pytest.fixture
def catalogue_db(monkeypatch, tmp_path):
    if not os.getenv("QUENYA_TEST_PG_URL"):
        monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "deck.sqlite")
    db.init_db()

    def insert(items):
        for offset in range(0, len(items), 200):
            chunk = items[offset:offset + 200]
            values = []
            for item in chunk:
                values.extend((
                    item["id"], item.get("source", "shop"),
                    f"https://example.test/{item['id']}", item["id"],
                    "https://example.test/image.jpg", item.get("fetched_at", "2026-01-01"),
                    "{}", json.dumps(item["tags"]) if "tags" in item else None,
                    item.get("availability"),
                ))
            with db.db_connection() as connection:
                connection.execute(
                    "INSERT INTO items(id, source, source_url, title, image_url, fetched_at, "
                    "raw_json, tags_json, availability) VALUES "
                    + ",".join(["(?,?,?,?,?,?,?,?,?)"] * len(chunk)),
                    tuple(values),
                )
        db.invalidate_live_catalogue()

    return insert


def _stable_legacy():
    items = sorted(db.live_catalogue(), key=lambda item: item["id"])
    items.sort(key=lambda item: item["fetched_at"], reverse=True)
    return phase2_api._deck_candidates(items)


@pytest.mark.parametrize("new,used,extras", [
    (0, 0, 0), (6000, 0, 0), (0, 2500, 0), (0, 0, 600),
    (6000, 100, 600), (6000, 2500, 600), (1, 1, 1),
])
def test_bounded_sql_matches_stable_full_catalogue(catalogue_db, new, used, extras):
    buckets = [
        [{"id": f"new-{i:05}", "tags": {"slot": "top", "condition": "new"}}
         for i in range(new)],
        [{"id": f"used-{i:05}", "tags": {"slot": "bottom", "condition": "second-hand"}}
         for i in range(used)],
        [{"id": f"extra-{i:05}", "tags": {"slot": "bag" if i % 2 else "accessory"}}
         for i in range(extras)],
    ]
    items = [item for group in zip_longest(*buckets) for item in group if item is not None]
    for index, item in enumerate(items):
        item["fetched_at"] = f"2026-01-{index % 3 + 1:02}"
    catalogue_db(list(reversed(items)))
    actual = db.deck_catalogue()
    assert actual == _stable_legacy()
    assert len(actual) <= 5500
    assert all("raw_json" not in item for item in actual)


def test_sql_preserves_condition_fallbacks_stock_and_tag_shapes(catalogue_db):
    items = []
    for source in ("shop", "vinted"):
        for index, condition in enumerate((None, "", False, 0, 0.0, [], {},
                                           "new", "second-hand", "used", "0", True, [1], {"x": 1})):
            for slot in ("top", "bag", "accessory", None, "outerwear"):
                items.append({"id": f"{source}-{index}-{slot}", "source": source,
                              "tags": {"slot": slot, "condition": condition}})
        for index, tags in enumerate(({}, [], None, 0, False, "string")):
            items.append({"id": f"{source}-shape-{index}", "source": source, "tags": tags})
    for availability in (None, "InStock", "OutOfStock", "SoldOut", "Discontinued", "outofstock"):
        items.append({"id": f"stock-{availability}", "tags": {"slot": "top"},
                      "availability": availability})
    items.append({"id": "untagged"})
    catalogue_db(items)
    assert db.deck_catalogue() == _stable_legacy()


def test_id_breaks_only_deck_timestamp_ties_in_binary_order(catalogue_db):
    ids = ["z", "A", "a", "é", "E", "a-1", "a_1"]
    catalogue_db([{"id": item_id, "tags": {"slot": "top"}} for item_id in ids])
    catalogue_db([{"id": "old", "tags": {"slot": "top"}, "fetched_at": "2025-01-01"}])
    assert [item["id"] for item in db.deck_catalogue()] == sorted(ids) + ["old"]


def test_deck_cold_load_is_one_bounded_query_and_does_not_warm_full_cache(
    catalogue_db, monkeypatch
):
    catalogue_db([{"id": f"item-{i:05}", "tags": {"slot": "top"}} for i in range(6000)])
    original_connection = db.db_connection
    calls = []
    fetched = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=()):
            calls.append(sql)
            cursor = self.connection.execute(sql, params)

            class Cursor:
                def fetchall(self):
                    rows = cursor.fetchall()
                    fetched.append(len(rows))
                    return rows

            return Cursor()

    @contextmanager
    def connection():
        with original_connection() as value:
            yield Connection(value)

    monkeypatch.setattr(db, "db_connection", connection)
    items = db.deck_catalogue()
    assert len(calls) == 1
    assert fetched == [5000]
    assert db._LIVE_CATALOGUE.items is None
    assert db.deck_catalogue() == items
    assert len(calls) == 1
    items[0]["title"] = "caller mutation"
    assert db.deck_catalogue()[0]["title"] != "caller mutation"


def test_sql_inputs_produce_identical_rankings_after_stable_order(catalogue_db):
    catalogue_db([{"id": f"item-{i}", "tags": {
        "slot": "top", "condition": "second-hand" if i % 3 else "new",
        "colours": ["navy"], "gender": "unisex",
    }} for i in range(50)])
    brief = {"gender": "menswear", "condition": "both", "budget_gbp": 100}
    profile = {"likes": {"colour:navy": 0.5}, "avoids": {}}
    expected = rank_items(_stable_legacy(), brief, profile)
    assert rank_items(db.deck_catalogue(), brief, profile) == expected


def test_explicit_invalidation_refreshes_both_caches_after_tag_writes(catalogue_db):
    assert db.live_catalogue() == db.deck_catalogue() == []
    item = {"id": "new", "source": "shop", "source_url": "https://example.test/new",
            "title": "New", "price_gbp": 20, "size": None,
            "image_url": "https://example.test/new.jpg"}
    db.insert_items([item])
    assert db.deck_catalogue() == []
    db.save_tags("new", {"slot": "top"}, None)
    assert db.deck_catalogue() == db.live_catalogue() == []
    db.invalidate_live_catalogue()
    assert [row["id"] for row in db.deck_catalogue()] == ["new"]
    assert [row["id"] for row in db.live_catalogue()] == ["new"]
    db.save_tags("new", {"slot": "bag", "condition": "second-hand"}, None)
    db.invalidate_live_catalogue()
    assert db.deck_catalogue()[0]["tags"]["slot"] == "bag"
    assert db.live_catalogue()[0]["tags"]["slot"] == "bag"
