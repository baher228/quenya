import json
import os
import random
from contextlib import contextmanager, nullcontext

import pytest

from app import db, outfits, phase2_api, worker
from app.rank import rank_items, tags_for
from app.schemas import OutfitRequest, SwapRequest


def _items(seed, count=600):
    rng = random.Random(seed)
    categories = [
        ("top", "T-shirt"), ("top", "shirt"), ("top", "polo"),
        ("top", "hoodie"), ("knitwear", "cardigan"), ("outerwear", "fleece"),
        ("outerwear", "rain jacket"), ("bottom", "trousers"), ("dress", "dress"),
        ("shoes", "trainers"), ("bag", "tote"), ("accessory", "scarf"),
        ("top", "sports bra"), ("top", "pyjamas"),
    ]
    items = []
    for index in range(count):
        slot, category = rng.choice(categories)
        items.append({
            "id": f"item-{index:04}", "source": rng.choice(["shop", "vinted"]),
            "title": category, "price_gbp": rng.choice([None, 10, 20, 70, 200]),
            "fetched_at": rng.choice(["2026-01-01", "2026-01-02"]),
            "source_url": f"https://shop.test/{index}",
            "description": "Original full record", "image_preview": f"preview-{index}",
            "tags": {"slot": slot, "category": category,
                     "gender": rng.choice(["menswear", "womenswear", "unisex"]),
                     "condition": rng.choice(["new", "second-hand"]),
                     "colours": [rng.choice(["navy", "red"])],
                     "niche": rng.choice([0, 0.5, 1])},
        })
    rng.shuffle(items)
    return items


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("size", [1, 37, 2000])
@pytest.mark.parametrize("mode,pieces,gender,shoes", [
    ("outfits", [], "menswear", True), ("outfits", [], "womenswear", False),
    ("pieces", ["top"], "both", True), ("pieces", ["outerwear"], "both", False),
    ("pieces", ["bottom", "shoes"], "both", True),
])
def test_batched_pool_exactly_matches_full_ranking(seed, size, mode, pieces, gender, shoes):
    items = _items(seed)
    brief = {"budget_gbp": 200, "condition": "both", "gender": gender,
             "looking_for": mode, "pieces": pieces, "include_shoes": shoes}
    profile = {"likes": {"colour:navy": 0.8}, "avoids": {"colour:red": 0.3},
               "niche_level": 0.5, "price_ceiling_gbp": 200}
    core = {"likes": {"category:shirt": 0.2}, "avoids": {}}
    locks = ["item-0001", "item-0029", "item-0575", "missing", "item-0001"]
    excluded = ["item-0001", "item-0008", "item-0010"]
    expected = outfits.build_pool(items, brief, profile, locks, excluded, core_profile=core)
    batches = (items[start:start + size] for start in range(0, len(items), size))
    assert outfits.build_pool_batches(
        batches, brief, profile, locks, excluded, core_profile=core
    ) == expected
    assert len(expected) <= 54 + len(set(locks))


def test_pool_reducer_only_retains_quotas_between_batches(monkeypatch):
    original = outfits.build_pool
    sizes = []

    def select(items, *args, **kwargs):
        sizes.append(len(items))
        return original(items, *args, **kwargs)

    monkeypatch.setattr(outfits, "build_pool", select)
    items = _items(3, 6000)
    outfits.build_pool_batches(
        (items[offset:offset + 100] for offset in range(0, len(items), 100)),
        {"budget_gbp": 200}, {}, [], [],
    )
    assert max(sizes) <= 154
    assert len(sizes) == 60


def test_empty_batches():
    assert outfits.build_pool_batches(iter([]), {}, {}, [], []) == []
    assert outfits.build_pool_batches([[], []], {}, {}, [], []) == []


def test_stream_uses_fetchmany_and_closes_on_early_exit(monkeypatch):
    events = []

    class Cursor:
        def execute(self, _sql):
            pass

        def fetchmany(self, size):
            events.append(size)
            return [{"id": "item-1", "tags_json": "{}"}]

        def close(self):
            events.append("closed")

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connection():
        try:
            yield Connection()
        finally:
            events.append("released")

    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "db_connection", connection)
    with pytest.raises(RuntimeError), db.live_catalogue_batches(7) as batches:
        assert next(batches)[0]["tags"] == {}
        raise RuntimeError("stop consuming")
    assert events == [7, "closed", "released"]


def test_stream_closes_after_decode_error(monkeypatch):
    events = []

    class Cursor:
        def execute(self, _sql):
            pass

        def fetchmany(self, _size):
            return [{"tags_json": "not json"}]

        def close(self):
            events.append("closed")

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "db_connection", connection)
    with pytest.raises(json.JSONDecodeError), db.live_catalogue_batches() as batches:
        next(batches)
    assert events == ["closed"]


def test_postgres_server_cursors_are_transaction_local_and_uniquely_named():
    statements = []
    names = []

    class RawConnection:
        def execute(self, sql, params=None):
            statements.append(sql)

        def cursor(self, *, name):
            names.append(name)
            return name

    connection = db._PostgresConnection(RawConnection())
    connection.server_cursor()
    connection.server_cursor()
    connection.execute("SELECT 1")
    assert statements == ["SET LOCAL search_path TO quenya, extensions", "SELECT 1"]
    assert len(set(names)) == 2


def test_stream_snapshot_matches_full_records_without_warming_cache(monkeypatch, tmp_path):
    if not os.getenv("QUENYA_TEST_PG_URL"):
        monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "catalogue.sqlite")
    db.init_db()
    with db.db_connection() as connection:
        for item in _items(0, 45):
            connection.execute(
                "INSERT INTO items(id,source,source_url,title,image_url,fetched_at,"
                "raw_json,tags_json) VALUES (?,?,?,?,?,?,?,?)",
                (item["id"], item["source"], item["source_url"], item["title"],
                 "https://images.test/a.jpg", item["fetched_at"], "{}", json.dumps(item["tags"])),
            )
    with db.live_catalogue_batches(7) as batches:
        actual = []
        for batch in batches:
            assert len(batch) <= 7
            actual.extend(batch)
    assert db._LIVE_CATALOGUE.items is None
    assert sorted(actual, key=lambda item: item["id"]) == sorted(
        db.live_catalogue(), key=lambda item: item["id"]
    )


def test_stream_rejects_non_positive_batch_size():
    with pytest.raises(ValueError), db.live_catalogue_batches(0):
        pass


def test_generation_closes_catalogue_before_model_work(monkeypatch):
    opened = False

    @contextmanager
    def batches():
        nonlocal opened
        opened = True
        try:
            yield [_items(0)]
        finally:
            opened = False

    class StopAfterPool(Exception):
        pass

    def model(*_args, **_kwargs):
        assert not opened
        assert db._LIVE_CATALOGUE.items is None
        raise StopAfterPool

    monkeypatch.setattr(phase2_api, "_board_profile", lambda _board_id: {
        "id": "owned-board", "brief": {"budget_gbp": 200},
    })
    monkeypatch.setattr(db, "latest_taste", lambda _profile_id: {
        "version": 1, "profile": {"price_ceiling_gbp": 200},
    })
    monkeypatch.setattr(db, "profile_swipe_items", lambda _profile_id: [])
    monkeypatch.setattr(db, "swipe_history", lambda _profile_id: {})
    monkeypatch.setattr(db, "live_catalogue_batches", batches)
    monkeypatch.setattr(db, "live_catalogue", lambda: pytest.fail("unbounded catalogue"))
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "stylist_user_content", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(phase2_api, "style_notes", lambda _brief: [])
    monkeypatch.setattr(phase2_api, "call_task", model)
    with pytest.raises(StopAfterPool):
        phase2_api.generate_outfits(OutfitRequest(board_id="owned-board"))


def test_idle_worker_does_not_warm_full_catalogue(monkeypatch):
    class Stopped:
        def clear(self):
            pass

        def is_set(self):
            return True

    monkeypatch.setattr(worker, "_stop", Stopped())
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "live_catalogue", lambda: pytest.fail("unbounded worker warmup"))
    worker.run_worker(1)


@pytest.mark.parametrize("size", [1, 7, 2000])
@pytest.mark.parametrize("slot", ["top", "bottom", "outerwear"])
def test_streamed_swap_matches_full_rank_and_subtype_interleave(monkeypatch, size, slot):
    items = _items(1)
    category = {"top": "T-shirt", "bottom": "trousers", "outerwear": "jacket"}[slot]
    replaced = {"id": "replaced", "price_gbp": 10, "title": category,
                "tags": {"slot": slot, "category": category}}
    outfit = {"id": "outfit", "profile_id": "board", "piece_ids": ["replaced"],
              "total_gbp": 10}
    brief = {"budget_gbp": 200, "gender": "both"}
    profile = {"price_ceiling_gbp": 200, "likes": {"colour:navy": 0.8}, "avoids": {}}
    excluded = [item["id"] for item in items[:120]]
    body = SwapRequest(board_id="board", item_id="replaced", exclude_ids=excluded)
    before = set(outfits.outfit_errors([replaced], True))
    ranked = [
        item for item in rank_items(items, brief, profile)
        if tags_for(item).get("slot") == slot
        and outfits.layer_role(item) == outfits.layer_role(replaced)
        and outfits.is_outfit_eligible(item)
        and item["price_gbp"] <= 200
        and not (set(outfits.outfit_errors([item], True)) - before)
    ]
    if outfits.layer_role(replaced) == "base":
        ranked = phase2_api._interleave_base_candidates(ranked)
    expected = next(item for item in ranked if item["id"] not in excluded)
    by_id = {item["id"]: item for item in [replaced, *items]}
    monkeypatch.setattr(phase2_api, "_board_profile", lambda _id: {"id": "board", "brief": brief})
    monkeypatch.setattr(phase2_api, "_taste", lambda _id: profile)
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(db, "get_outfit", lambda _id: outfit)
    monkeypatch.setattr(db, "get_item_with_tags", by_id.get)
    monkeypatch.setattr(
        db,
        "get_items_with_tags",
        lambda item_ids: {
            item_id: by_id[item_id] for item_id in item_ids if item_id in by_id
        },
    )
    monkeypatch.setattr(db, "profile_swipe_items", lambda _id: [])
    monkeypatch.setattr(db, "live_catalogue_batches", lambda: nullcontext(
        items[offset:offset + size] for offset in range(0, len(items), size)
    ))
    monkeypatch.setattr(db, "update_outfit_pieces", lambda _id, ids, total: outfit.update(
        piece_ids=ids, total_gbp=total
    ))
    monkeypatch.setattr(phase2_api, "_public_outfit", lambda result: result)
    assert phase2_api.swap("outfit", body)["piece_ids"] == [expected["id"]]
    assert db._LIVE_CATALOGUE.items is None


@pytest.mark.parametrize("size", [1, 7, 2000])
@pytest.mark.parametrize("limit,slot", [(0, None), (20, "top"), (101, None)])
def test_streamed_search_matches_full_results(monkeypatch, size, limit, slot):
    items = _items(2)
    brief = {"budget_gbp": 200, "gender": "both"}
    profile = {"price_ceiling_gbp": 200, "likes": {"colour:navy": 0.8}, "avoids": {}}
    matches = [item for item in items if "shirt" in item["title"].lower()
               and (slot is None or tags_for(item).get("slot") == slot)]
    expected = rank_items(matches, brief, profile)[:max(1, min(limit, 100))]
    monkeypatch.setattr(phase2_api, "_user_profile", lambda: {"id": "board", "brief": brief})
    monkeypatch.setattr(phase2_api, "_taste", lambda _id: profile)
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "_public_item", lambda result: result)
    monkeypatch.setattr(db, "live_catalogue_batches", lambda: nullcontext(
        items[offset:offset + size] for offset in range(0, len(items), size)
    ))
    assert phase2_api.keyword_search(" SHIRT ", slot, limit) == expected
    assert db._LIVE_CATALOGUE.items is None
