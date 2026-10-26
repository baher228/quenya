from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app import db, phase2_api


def _brief(**overrides):
    return {
        "text": "relaxed workwear",
        "gender": "both",
        "condition": "both",
        "budget_gbp": 200,
        **overrides,
    }


def _item(
    item_id: str,
    *,
    source: str = "retail",
    condition: str = "new",
    query: str | None = None,
    availability: str | None = None,
):
    return {
        "id": item_id,
        "source": source,
        "source_url": f"https://example.test/{item_id}",
        "title": item_id,
        "price_gbp": 20,
        "image_url": f"https://images.example/{item_id}.jpg",
        "image_path": None,
        "gender_hint": "u",
        "condition_hint": condition,
        "availability": availability,
        "query": query,
        "tags": {
            "slot": "top",
            "category": "shirt",
            "condition": condition,
            "aesthetics": ["casual"],
        },
    }


def test_profile_live_items_reuses_live_catalogue_filters(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "profile-live.sqlite")
    db.init_db()
    records = [
        _item("board-live", source="vinted", query="live:board"),
        _item("other-board", source="vinted", query="live:other"),
        _item(
            "out-of-stock",
            source="vinted",
            query="live:board",
            availability="https://schema.org/OutOfStock",
        ),
        _item("untagged", source="vinted", query="live:board"),
    ]
    db.insert_items(records)
    for item in records[:3]:
        db.save_tags(item["id"], item["tags"], None)

    live = db.profile_live_items("board")

    assert [item["id"] for item in live] == ["board-live"]
    assert live[0]["query"] == "live:board"
    assert isinstance(live[0]["tags"], dict)


def test_tag_pending_live_tags_only_the_requested_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pending-live.sqlite")
    db.init_db()
    records = [
        _item("pending", source="vinted", query="live:board"),
        _item("other", source="vinted", query="live:other"),
    ]
    db.insert_items(records)
    calls = []
    tags = SimpleNamespace(model_dump=lambda: {"slot": "top", "condition": "new"})
    meta = SimpleNamespace(request_id="tag-request")

    def fake_tag(item):
        calls.append(item["id"])
        return tags, meta, None

    monkeypatch.setattr(phase2_api, "tag_item_with_vectors", fake_tag)

    tagged, failed, errors, domains = phase2_api._tag_pending_live("board")

    assert calls == ["pending"]
    assert (tagged, failed, errors) == (1, 0, [])
    assert domains == {
        "vinted": {"inserted": 0, "tagged": 1, "failed": 0}
    }
    assert [item["id"] for item in db.live_catalogue()] == ["pending"]


def test_fetch_jobs_filters_by_profile_and_kind(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fetch-jobs.sqlite")
    db.init_db()
    db.save_fetch_job("refill-1", "board", {"kind": "vinted_refill", "page": 1})
    db.save_fetch_job("fetch-1", "board", {"kind": "initial_fetch"})
    db.save_fetch_job("other-board", "other", {"kind": "vinted_refill"})

    assert len(db.fetch_jobs("board")) == 2
    assert db.fetch_jobs("board", kind="vinted_refill") == [
        {"kind": "vinted_refill", "page": 1}
    ]


def test_fetch_profile_items_keeps_retail_results_when_vinted_fails(
    monkeypatch,
):
    brief = _brief()
    job = {"id": "fetch-1", "errors": []}
    profile = {"id": "board", "brief": brief}
    retail_item = _item("retail-item", source="retail.example")
    inserted_records = []
    monkeypatch.setattr(db, "get_fetch_job", lambda _job_id: job)
    monkeypatch.setattr(db, "update_fetch_job", lambda *_args: None)
    monkeypatch.setattr(db, "get_profile", lambda _profile_id: profile)
    monkeypatch.setattr(db, "latest_taste", lambda _profile_id: None)
    monkeypatch.setattr(db, "source_urls", lambda: set())
    monkeypatch.setattr(
        db,
        "insert_items",
        lambda records: inserted_records.extend(records) or len(records),
    )
    monkeypatch.setattr(phase2_api.catalog, "is_old_money", lambda *_args: False)
    monkeypatch.setattr(phase2_api.catalog, "CATALOG", {})
    monkeypatch.setattr(
        phase2_api, "CONTEXT_SOURCES", {"other": ["vinted", "retail.example"]}
    )
    monkeypatch.setattr(
        phase2_api.retail, "RETAIL_DOMAINS", {"retail.example"}
    )
    monkeypatch.setattr(
        phase2_api.vinted,
        "search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("temporary 500")),
    )
    monkeypatch.setattr(
        phase2_api.retail, "search", lambda *_args, **_kwargs: [retail_item]
    )
    monkeypatch.setattr(
        phase2_api,
        "_tag_pending_live",
        lambda _profile_id: (
            1,
            0,
            [],
            {
                "retail.example": {
                    "inserted": 0,
                    "tagged": 1,
                    "failed": 0,
                }
            },
        ),
    )

    phase2_api._fetch_profile_items(
        "fetch-1", "board", ["workwear"], "both", brief=brief
    )

    assert job["status"] == "done"
    assert job["errors"] == ["vinted: temporary 500"]
    assert [item["id"] for item in inserted_records] == ["retail-item"]
    assert inserted_records[0]["query"] == "live:board"


def test_refill_vinted_inserts_live_records_and_tags_pending_items(monkeypatch):
    brief = _brief(text="third query")
    job = {
        "id": "refill-1",
        "started_at": "2026-01-01T00:00:00+00:00",
        "status": "running",
        "fetched": 0,
        "tagged": 0,
        "failed": 0,
        "errors": [],
        "kind": "vinted_refill",
        "page": 4,
    }
    inserted_records = []
    search_calls = []
    tagged_profiles = []
    final_jobs = []

    def fake_search(query, gender, per_page, page):
        search_calls.append((query, gender, per_page, page))
        if query == "second":
            raise RuntimeError("temporary 500")
        return [_item(f"vinted-{query}", source="vinted", condition="second-hand")]

    monkeypatch.setattr(db, "get_fetch_job", lambda _job_id: job)
    monkeypatch.setattr(
        db, "get_profile", lambda _profile_id: {"brief": brief}
    )
    monkeypatch.setattr(
        db,
        "latest_taste",
        lambda _profile_id: {
            "profile": {"search_queries": ["first", "second", "first"]}
        },
    )
    monkeypatch.setattr(phase2_api.vinted, "search", fake_search)
    monkeypatch.setattr(
        db,
        "insert_items",
        lambda records: inserted_records.extend(records) or len(records),
    )
    monkeypatch.setattr(
        phase2_api,
        "_tag_pending_live",
        lambda profile_id: (
            tagged_profiles.append(profile_id) or 2,
            0,
            [],
            {"vinted": {"inserted": 0, "tagged": 2, "failed": 0}},
        ),
    )
    monkeypatch.setattr(
        db,
        "update_fetch_job",
        lambda _job_id, state: final_jobs.append(state.copy()),
    )

    phase2_api._refill_vinted("refill-1", "board", 4)

    assert search_calls == [
        ("first", "u", 20, 4),
        ("second", "u", 20, 4),
        ("third query", "u", 20, 4),
    ]
    assert len(inserted_records) == 2
    assert all(record["query"] == "live:board" for record in inserted_records)
    assert tagged_profiles == ["board"]
    assert final_jobs[-1]["status"] == "done"
    assert final_jobs[-1]["page"] == 4
    assert final_jobs[-1]["fetched"] == 2
    assert final_jobs[-1]["tagged"] == 2
    assert final_jobs[-1]["failed"] == 0
    assert final_jobs[-1]["errors"] == ["vinted: temporary 500"]
    assert final_jobs[-1]["domain_counts"]["vinted"]["inserted"] == 2


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, swiped):
        self._swiped = swiped

    def execute(self, *_args):
        return _Rows([(item_id,) for item_id in self._swiped])


def _run_deck(
    monkeypatch,
    *,
    cached_items,
    base_deck,
    ranked_items=None,
    live_items=(),
    swiped=(),
    jobs=(),
    condition="both",
    brief_condition=None,
    arm="tags",
):
    brief = _brief(condition=brief_condition or condition)
    active = {"id": "board", "brief": brief, "context": "city"}
    taste = {"niche_level": 0.5, "price_ceiling_gbp": 200}
    if condition is not None:
        taste["condition_preference"] = condition
    ranked_items = ranked_items or [*cached_items, *live_items]
    rank_inputs = []
    saved_jobs = []
    events = []
    fetch_calls = []
    background_tasks = BackgroundTasks()
    monkeypatch.setenv("QUENYA_RANKER", arm)
    monkeypatch.setattr(phase2_api, "_user_profile", lambda: active)
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: taste)
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(db, "db_connection", lambda: nullcontext(_Connection(swiped)))
    monkeypatch.setattr(db, "live_catalogue", lambda: list(cached_items))
    monkeypatch.setattr(
        db, "profile_live_items", lambda _profile_id: list(live_items)
    )
    monkeypatch.setattr(
        db,
        "fetch_jobs",
        lambda profile_id, kind=None: fetch_calls.append((profile_id, kind)) or list(jobs),
    )
    monkeypatch.setattr(
        db,
        "save_fetch_job",
        lambda job_id, profile_id, state: saved_jobs.append(
            (job_id, profile_id, state.copy())
        ),
    )
    monkeypatch.setattr(db, "now_iso", lambda: "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(db, "next_deck_request", lambda _profile_id: 1)
    monkeypatch.setattr(db, "profile_swipes", lambda _profile_id: [])
    monkeypatch.setattr(db, "recent_deck_item_ids", lambda _profile_id: [])
    monkeypatch.setattr(db, "record_deck_events", lambda rows: events.extend(rows))

    def rank(items, *_args, **_kwargs):
        rank_inputs.append([item["id"] for item in items])
        return list(ranked_items)

    monkeypatch.setattr(phase2_api, "rank_items", rank)
    monkeypatch.setattr(
        phase2_api, "make_deck", lambda *_args, **_kwargs: list(base_deck)
    )
    monkeypatch.setattr(
        phase2_api, "add_exploration", lambda deck, *_args, **_kwargs: list(deck)
    )
    monkeypatch.setattr(phase2_api, "_public_item", lambda item: item)
    monkeypatch.setattr(phase2_api, "score_item", lambda *_args: 0.0)

    if arm == "embed":
        vector = [1.0]
        monkeypatch.setattr(
            db,
            "item_mix_vectors",
            lambda item_ids: {item_id: {"mix": vector} for item_id in item_ids},
        )
        monkeypatch.setattr(db, "profile_embedding", lambda _profile_id: vector)
        monkeypatch.setattr(db, "nearest_item_mix", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(phase2_api, "history_query", lambda *_args: None)
        monkeypatch.setattr(phase2_api, "catalogue_sample", lambda: [])
        monkeypatch.setattr(
            phase2_api,
            "rank_candidates",
            lambda *_args, **_kwargs: (list(ranked_items), {}, None),
        )

    result = phase2_api.deck(len(base_deck), background_tasks=background_tasks)
    return result, background_tasks, saved_jobs, events, fetch_calls, rank_inputs


def test_deck_schedules_refill_when_unseen_live_vinted_items_are_low(monkeypatch):
    cards = [_item(f"retail-{index}") for index in range(10)]

    result, tasks, saved, _events, fetch_calls, _rank_inputs = _run_deck(
        monkeypatch, cached_items=cards, base_deck=cards
    )

    assert len(result) == 10
    assert fetch_calls == [("board", "vinted_refill")]
    assert len(saved) == 1
    assert saved[0][2]["kind"] == "vinted_refill"
    assert saved[0][2]["page"] == 1
    assert tasks.tasks[0].func is phase2_api._refill_vinted
    assert tasks.tasks[0].args == (saved[0][0], "board", 1)


def test_deck_does_not_schedule_during_a_recent_running_refill(monkeypatch):
    running = {
        "status": "running",
        "started_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
    }
    cards = [_item(f"retail-{index}") for index in range(10)]

    _result, tasks, saved, _events, _fetch_calls, _rank_inputs = _run_deck(
        monkeypatch, cached_items=cards, base_deck=cards, jobs=[running]
    )

    assert saved == []
    assert tasks.tasks == []


@pytest.mark.parametrize(
    ("condition", "brief_condition"),
    [("new", "both"), (None, "new")],
)
def test_deck_does_not_schedule_when_condition_is_new(
    monkeypatch, condition, brief_condition
):
    cards = [_item(f"retail-{index}") for index in range(10)]

    _result, tasks, saved, _events, fetch_calls, _rank_inputs = _run_deck(
        monkeypatch,
        cached_items=cards,
        base_deck=cards,
        condition=condition,
        brief_condition=brief_condition,
    )

    assert saved == []
    assert tasks.tasks == []
    assert fetch_calls == []


def test_deck_refill_page_rotates_over_previous_refill_jobs(monkeypatch):
    now = datetime.now(UTC)
    jobs = [
        {
            "status": "done",
            "started_at": (now - timedelta(minutes=index + 1)).isoformat(),
        }
        for index in range(7)
    ]
    cards = [_item(f"retail-{index}") for index in range(10)]

    _result, _tasks, saved, _events, _fetch_calls, _rank_inputs = _run_deck(
        monkeypatch, cached_items=cards, base_deck=cards, jobs=jobs
    )

    assert saved[0][2]["page"] == 3


def test_deck_treats_running_refills_older_than_five_minutes_as_stale(monkeypatch):
    stale = {
        "status": "running",
        "started_at": (datetime.now(UTC) - timedelta(minutes=6)).isoformat(),
    }
    cards = [_item(f"retail-{index}") for index in range(10)]

    _result, tasks, saved, _events, _fetch_calls, _rank_inputs = _run_deck(
        monkeypatch, cached_items=cards, base_deck=cards, jobs=[stale]
    )

    assert len(saved) == 1
    assert saved[0][2]["page"] == 2
    assert tasks.tasks[0].func is phase2_api._refill_vinted


@pytest.mark.parametrize(
    ("arm", "include_global_secondhand"),
    [
        ("tags", False),
        ("embed", False),
        ("tags", True),
        ("embed", True),
    ],
)
def test_deck_tops_up_to_two_low_ranked_own_live_vinted_items(
    monkeypatch, arm, include_global_secondhand
):
    retail_cards = [_item(f"retail-{index}") for index in range(10)]
    global_secondhand = [
        _item(f"global-vinted-{index}", source="vinted", condition="second-hand")
        for index in range(2)
    ]
    cards = (
        [*retail_cards[:8], *global_secondhand]
        if include_global_secondhand
        else retail_cards
    )
    live_items = [
        _item(
            f"live-vinted-{index}",
            source="vinted",
            condition="second-hand",
            query="live:board",
        )
        for index in range(5)
    ]
    ranked = [*cards, *live_items]
    cached = [*cards, live_items[1]]
    swiped = {"live-vinted-0"}

    result, _tasks, _saved, events, _fetch_calls, rank_inputs = _run_deck(
        monkeypatch,
        cached_items=cached,
        base_deck=cards,
        ranked_items=ranked,
        live_items=live_items,
        swiped=swiped,
        arm=arm,
    )

    result_ids = [item["id"] for item in result]
    live_result_ids = [
        item_id for item_id in result_ids if item_id.startswith("live-vinted-")
    ]
    assert len(result) == 10
    assert len(set(result_ids)) == 10
    assert len(live_result_ids) == 2
    assert set(live_result_ids) == {"live-vinted-1", "live-vinted-2"}
    assert "live-vinted-0" not in result_ids
    if include_global_secondhand:
        assert not any(item_id.startswith("global-vinted-") for item_id in result_ids)
    assert len(events) == 10
    assert len(rank_inputs[0]) == len(set(rank_inputs[0]))


@pytest.mark.parametrize("arm", ["tags", "embed"])
def test_deck_preserves_existing_own_live_vinted_items(monkeypatch, arm):
    retail_cards = [_item(f"retail-{index}") for index in range(7)]
    live_items = [
        _item(
            f"live-vinted-{index}",
            source="vinted",
            condition="second-hand",
            query="live:board",
        )
        for index in range(3)
    ]
    deck = [*retail_cards, *live_items]

    result, _tasks, _saved, _events, _fetch_calls, _rank_inputs = _run_deck(
        monkeypatch,
        cached_items=deck,
        base_deck=deck,
        ranked_items=deck,
        live_items=live_items,
        arm=arm,
    )

    assert [item["id"] for item in result] == [item["id"] for item in deck]
