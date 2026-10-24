import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import db, phase2_api, routing, taste
from app.config import CONTEXT_SOURCES
from app.core import shared_core
from app.outfits import build_pool, stylist_user_content, validate_outfits
from app.rank import make_deck
from app.schemas import (
    BoardBriefRequest,
    ProfileBrief,
    ProfileCreateRequest,
    RouteCorrectionRequest,
    RouteOut,
    SwipeRequest,
    TasteProfile,
    UploadAssignRequest,
)
from app.sources import sport
from app.sources.sport import parse_product_html

FIXTURES = Path(__file__).parent / "fixtures"


def _brief(**overrides):
    return {
        "text": "Relaxed city workwear",
        "looking_for": "outfits",
        "pieces": ["outerwear", "top", "bottom"],
        "include_shoes": True,
        "gender": "both",
        "condition": "both",
        "niche": 0.4,
        "budget_gbp": 180,
        "sizes": {"tops": "M"},
        **overrides,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tech bro: quarter-zips, …", "Tech bro"),
        ("  baggy 90s skate look with washed hoodies", "Baggy 90s skate look"),
    ],
)
def test_label_from_brief(text, expected):
    assert phase2_api._label_from_brief(text) == expected


def _profile(**overrides):
    return {
        "summary": "A practical style",
        "likes": {"workwear": 0.8, "earthy tones": 0.6},
        "avoids": {"large logos": 0.7},
        "rules": ["Avoid large logos", "Prefer relaxed workwear"],
        "palette": ["olive", "cream"],
        "niche_level": 0.4,
        "price_ceiling_gbp": 180,
        "condition_preference": "both",
        "search_queries": ["relaxed workwear"],
        "changes": [],
        **overrides,
    }


def _item(item_id, slot, aesthetics=None):
    categories = {
        "top": "shirt",
        "bottom": "trousers",
        "shoes": "boots",
        "outerwear": "jacket",
        "dress": "dress",
    }
    return {
        "id": item_id,
        "price_gbp": 30,
        "tags": {
            "slot": slot,
            "category": categories[slot],
            "aesthetics": aesthetics or ["workwear"],
            "niche": 0.3,
            "gender": "unisex",
            "condition": "second-hand",
        },
    }


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "boards.sqlite")
    db.init_db()


def _create_board(board_id, *, context="city", brief=None, profile=None, active=True):
    board_brief = brief or _brief()
    db.save_taste_version(board_id, 1, profile or _profile(), {}, "mreq-v1")
    db.create_profile(
        board_id,
        board_brief,
        label=board_id.title(),
        context=context,
        active=active,
    )


def test_router_content_empty_sections_caps_and_order():
    empty = routing.router_user_content("brief", "city wardrobe", [], [])
    assert empty == (
        "Existing boards:\nnone\nPast corrections:\nnone\n"
        "New brief:\ncity wardrobe"
    )
    boards = [
        {"id": f"b{index}", "label": "Label", "context": "city", "brief": {"text": "brief"}}
        for index in range(13)
    ]
    corrections = [
        {
            "input_text": f"phrase-{index}",
            "corrected_to": {"context": "gym", "label": "Gym"},
        }
        for index in range(9)
    ]
    content = routing.router_user_content("photo", "blue jacket", boards, corrections)
    assert content.count("- id=") == 12
    assert content.count(" -> context=") == 8
    assert content.endswith("New photo:\nblue jacket")


def test_router_requires_a_listed_board_and_confidence(monkeypatch):
    board = {"id": "board-1", "context": "city", "label": "City", "brief": _brief()}
    monkeypatch.setattr(db, "profile_boards", lambda _kind: [board])
    monkeypatch.setattr(db, "corrected_routes", lambda _kind, _limit: [])

    def route(board_id, confidence):
        output = RouteOut(
            context="gym",
            board_id=board_id,
            label="Gym",
            confidence=confidence,
            reason="Test",
        )
        monkeypatch.setattr(
            routing,
            "call_task",
            lambda *_args: (output, SimpleNamespace(request_id="mreq-router")),
        )
        return routing.route_input("brief", "test")

    selected = route("board-1", 0.6)[1]
    assert selected["id"] == "board-1"
    assert route("unlisted", 0.9)[1] is None
    assert route("board-1", 0.59)[1] is None


def test_existing_brief_route_activates_board_without_new_taste(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _create_board("board-1", active=False)
    existing = db.get_profile("board-1")
    previous_taste = db.latest_taste("board-1")
    output = RouteOut(
        context="city",
        board_id="board-1",
        label="City",
        confidence=0.9,
        reason="Matches city",
    )
    monkeypatch.setattr(
        phase2_api,
        "route_input",
        lambda *_args: (output, existing, SimpleNamespace(request_id="mreq-r"), "content"),
    )
    monkeypatch.setattr(phase2_api, "_start_fetch", lambda *_args: "job-1")
    monkeypatch.setattr(
        phase2_api,
        "create_taste",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected taste call")),
    )
    result = phase2_api.create_profile(
        ProfileCreateRequest(brief=ProfileBrief.model_validate(_brief(text="new city brief"))),
        phase2_api.BackgroundTasks(),
    )
    assert result["profile_id"] == "board-1"
    assert db.active_profile()["id"] == "board-1"
    assert db.unconsumed_signals("board-1")["corrections"][0]["kind"] == "brief"
    assert result["route"]["routed"] == "existing"
    assert db.get_profile("board-1")["brief"]["text"] == "new city brief"
    assert db.latest_taste("board-1")["version"] == previous_taste["version"]


def test_new_board_flag_ignores_existing_route(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _create_board("existing", context="city", active=True)
    previous = db.get_profile("existing")
    previous_taste = db.latest_taste("existing")
    output = RouteOut(
        context="city",
        board_id="existing",
        label="Japanese street style",
        confidence=0.95,
        reason="Matches the existing style",
    )
    monkeypatch.setattr(
        phase2_api,
        "route_input",
        lambda *_args: (
            output,
            previous,
            SimpleNamespace(request_id="mreq-router"),
            "content",
        ),
    )
    monkeypatch.setattr(phase2_api, "_start_fetch", lambda *_args: "job-1")
    monkeypatch.setattr(
        taste,
        "call_task",
        lambda *_args: (
            TasteProfile.model_validate(_profile()),
            SimpleNamespace(request_id="mreq-taste", latency_ms=10),
        ),
    )
    result = phase2_api.create_profile(
        ProfileCreateRequest(
            brief=ProfileBrief.model_validate(
                _brief(text="Tech bro: quarter-zips, New Balance trainers, blue jeans")
            ),
            new_board=True,
        ),
        phase2_api.BackgroundTasks(),
    )

    assert result["profile_id"] != "existing"
    assert result["board"]["label"] == "Tech bro"
    assert result["route"]["label"] == "Tech bro"
    assert len(db.profile_boards()) == 2
    assert db.get_profile("existing")["brief"] == previous["brief"]
    assert db.latest_taste("existing")["version"] == previous_taste["version"]
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT output_json FROM routes WHERE id=?", (result["route"]["id"],)
        ).fetchone()
    route_output = json.loads(row["output_json"])
    assert route_output["_routed"] == "new"
    assert route_output["_forced_new"] is True
    assert route_output["label"] == "Tech bro"


def test_new_board_label_deduplicates_existing_user_board(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    db.save_taste_version("existing", 1, _profile(), {}, "mreq-v1")
    db.create_profile("existing", _brief(), label="tech bro", context="city")
    existing = db.get_profile("existing")
    output = RouteOut(
        context="city",
        board_id="existing",
        label="Tech bro",
        confidence=0.95,
        reason="Matches the existing style",
    )
    monkeypatch.setattr(
        phase2_api,
        "route_input",
        lambda *_args: (
            output,
            existing,
            SimpleNamespace(request_id="mreq-router"),
            "content",
        ),
    )
    monkeypatch.setattr(phase2_api, "_start_fetch", lambda *_args: "job-1")
    monkeypatch.setattr(
        taste,
        "call_task",
        lambda *_args: (
            TasteProfile.model_validate(_profile()),
            SimpleNamespace(request_id="mreq-taste", latency_ms=10),
        ),
    )

    result = phase2_api.create_profile(
        ProfileCreateRequest(
            brief=ProfileBrief.model_validate(_brief(text="Tech bro: new style")),
            new_board=True,
        ),
        phase2_api.BackgroundTasks(),
    )

    assert result["board"]["label"] == "Tech bro 2"
    assert result["route"]["label"] == "Tech bro 2"


def test_update_board_brief_updates_only_target_board(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _create_board("a", active=False)
    _create_board("b", active=True)
    db.insert_items(
        [
            {
                "id": "b-item",
                "source": "vinted",
                "source_url": "https://vinted.test/b-item",
                "title": "shirt",
                "price_gbp": 20,
                "image_url": "https://image.test/b-item.jpg",
                "raw_json": {},
            }
        ]
    )
    db.save_tags("b-item", _item("b-item", "top")["tags"], "mreq-tag")
    db.insert_swipe("b", "b-item", True)
    previous_b_brief = db.get_profile("b")["brief"]
    previous_b_taste = db.latest_taste("b")
    previous_b_swipes = db.unconsumed_signals("b")["swipes"]
    started = {}

    calls = []

    def fake_taste_call(_task, _system, _model, content, _output_model):
        calls.append(content)
        return (
            TasteProfile.model_validate(_profile(changes=["Refined the brief"])),
            SimpleNamespace(request_id="mreq-update", latency_ms=12),
        )

    monkeypatch.setattr(taste, "call_task", fake_taste_call)
    monkeypatch.setattr(
        phase2_api,
        "_start_fetch",
        lambda profile, queries, _background: (
            started.update(profile=profile, queries=queries) or "job-update"
        ),
    )
    updated_brief = ProfileBrief.model_validate(
        _brief(text="A sharper city wardrobe")
    )
    result = phase2_api.update_board_brief(
        "a", BoardBriefRequest(brief=updated_brief), phase2_api.BackgroundTasks()
    )

    assert result["profile_id"] == "a"
    assert result["route"] is None
    assert result["version"] == 2
    assert result["request_id"] == "mreq-update"
    assert db.get_profile("a")["brief"]["text"] == "A sharper city wardrobe"
    assert db.active_profile()["id"] == "a"
    assert db.latest_taste("a")["version"] == 2
    assert db.get_profile("b")["brief"] == previous_b_brief
    assert db.latest_taste("b") == previous_b_taste
    assert db.unconsumed_signals("b")["swipes"] == previous_b_swipes
    assert "A sharper city wardrobe" in started["queries"]
    assert any(
        'New signals:\n- brief: "A sharper city wardrobe"' in content
        for content in calls
    )
    assert db.unconsumed_signals("a")["corrections"] == []


@pytest.mark.parametrize("board_id", ["unknown", "warmup-board"])
def test_update_board_brief_returns_404_for_unknown_or_non_user(
    monkeypatch, tmp_path, board_id
):
    _db(monkeypatch, tmp_path)
    db.create_profile("warmup-board", _brief(), kind="warmup")
    with pytest.raises(HTTPException) as error:
        phase2_api.update_board_brief(
            board_id,
            BoardBriefRequest(brief=ProfileBrief.model_validate(_brief())),
            phase2_api.BackgroundTasks(),
        )
    assert error.value.status_code == 404


def test_forced_board_creation_and_taste_updates_are_board_local(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    def fake_route_input(*_args):
        existing = next(iter(db.profile_boards()), None)
        output = RouteOut(
            context="gym" if existing else "city",
            board_id=existing["id"] if existing else None,
            label="B" if existing else "A",
            confidence=0.95,
            reason="Matches an existing board" if existing else "First board",
        )
        return output, existing, SimpleNamespace(request_id="mreq-router"), "content"

    monkeypatch.setattr(phase2_api, "route_input", fake_route_input)
    monkeypatch.setattr(
        taste,
        "call_task",
        lambda *_args: (
            TasteProfile.model_validate(_profile()),
            SimpleNamespace(request_id="mreq-taste", latency_ms=5),
        ),
    )
    monkeypatch.setattr(phase2_api, "_start_fetch", lambda *_args: "job")
    for index in range(4):
        item_id = f"item-{index}"
        db.insert_items(
            [
                {
                    "id": item_id,
                    "source": "vinted",
                    "source_url": f"https://vinted.test/{item_id}",
                    "title": item_id,
                    "price_gbp": 20,
                    "image_url": f"https://image.test/{item_id}.jpg",
                    "raw_json": {},
                }
            ]
        )
        db.save_tags(item_id, _item(item_id, "top")["tags"], "mreq-tag")

    request = phase2_api.BackgroundTasks()
    created_a = phase2_api.create_profile(
        ProfileCreateRequest(
            brief=ProfileBrief.model_validate(_brief(text="Board A")),
            new_board=True,
        ),
        request,
    )
    created_b = phase2_api.create_profile(
        ProfileCreateRequest(
            brief=ProfileBrief.model_validate(_brief(text="Board B")),
            new_board=True,
        ),
        request,
    )
    assert created_a["profile_id"] != created_b["profile_id"]
    assert len(db.profile_boards()) == 2

    phase2_api.activate_board(created_a["profile_id"])
    for index in range(3):
        phase2_api.swipe(SwipeRequest(item_id=f"item-{index}", liked=True))
    phase2_api.activate_board(created_b["profile_id"])
    phase2_api.swipe(SwipeRequest(item_id="item-3", liked=False))
    result = phase2_api.taste_update()

    counts = {board["id"]: board["counts"]["swipes"] for board in phase2_api.boards()}
    assert counts == {created_a["profile_id"]: 3, created_b["profile_id"]: 1}
    assert result["version"] == 2
    assert db.latest_taste(created_a["profile_id"])["version"] == 1
    assert len(db.unconsumed_signals(created_a["profile_id"])["swipes"]) == 3


@pytest.mark.parametrize(
    ("board_id", "confidence"),
    [("unlisted", 0.9), ("existing", 0.59)],
)
def test_invalid_or_low_confidence_brief_route_creates_board(
    monkeypatch, tmp_path, board_id, confidence
):
    _db(monkeypatch, tmp_path)
    _create_board("existing", active=True)
    output = RouteOut(
        context="gym",
        board_id=board_id,
        label="Gym",
        confidence=confidence,
        reason="New context",
    )
    monkeypatch.setattr(
        routing,
        "call_task",
        lambda *_args: (output, SimpleNamespace(request_id="mreq-router")),
    )
    monkeypatch.setattr(phase2_api, "route_input", routing.route_input)
    monkeypatch.setattr(phase2_api, "_start_fetch", lambda *_args: "job-1")

    def fake_create_taste(brief, profile_id):
        profile = _profile()
        db.save_taste_version(profile_id, 1, profile, {}, "mreq-taste")
        return profile, SimpleNamespace(request_id="mreq-taste")

    monkeypatch.setattr(phase2_api, "create_taste", fake_create_taste)
    result = phase2_api.create_profile(
        ProfileCreateRequest(brief=ProfileBrief.model_validate(_brief(text="gym brief"))),
        phase2_api.BackgroundTasks(),
    )
    assert result["profile_id"] != "existing"
    assert len(db.profile_boards()) == 2


def test_swipes_and_deck_exclusions_are_board_local(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _create_board("a", active=True)
    _create_board("b", active=False)
    item = _item("item-1", "top")
    db.insert_items(
        [{
            "id": "item-1",
            "source": "vinted",
            "source_url": "https://vinted.test/1",
            "title": "shirt",
            "price_gbp": 20,
            "image_url": "https://image.test/1.jpg",
            "raw_json": {},
        }]
    )
    db.save_tags("item-1", item["tags"], "mreq-tag")
    db.insert_swipe("a", "item-1", True)
    assert db.unconsumed_signals("b")["swipes"] == []
    monkeypatch.setattr(phase2_api, "_user_profile", lambda: db.get_profile("b"))
    profile = _profile()
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: profile)
    monkeypatch.setattr(phase2_api.db, "list_items", lambda **_kwargs: [item])
    assert [entry["id"] for entry in phase2_api.deck(1)] == ["item-1"]


def test_corrected_route_is_in_next_router_context(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    route_id = db.create_route(
        "user", "brief", "more gym layers", {"context": "city"}, "mreq", None
    )
    db.correct_route(
        route_id, {"board_id": "gym-board", "context": "gym", "label": "Gym"}
    )
    content = routing.router_user_content(
        "brief", "lifting tops", [], db.corrected_routes()
    )
    assert '"more gym layers" -> context=gym | label=Gym' in content


def test_route_correction_existing_to_new_and_new_to_existing(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    original_brief = _brief(text="city clothes")
    _create_board("city", brief=original_brief, active=True)
    changed_brief = _brief(text="more autumn city layers")
    existing_route = db.create_route(
        "user",
        "brief",
        changed_brief["text"],
        {
            "_routed": "existing",
            "input_brief": changed_brief,
            "_previous_brief": original_brief,
            "context": "city",
            "label": "City",
        },
        "mreq-route-1",
        "city",
    )
    db.update_profile_brief("city", changed_brief)
    db.add_correction(
        "city", "brief", existing_route, original_brief, changed_brief,
        changed_brief["text"], "mreq-route-1",
    )

    def fake_create_taste(brief, profile_id):
        result = _profile()
        db.save_taste_version(profile_id, 1, result, {}, "mreq-taste")
        return result, SimpleNamespace(request_id="mreq-taste")

    monkeypatch.setattr(phase2_api, "create_taste", fake_create_taste)
    corrected = phase2_api.correct_route(existing_route, RouteCorrectionRequest())
    assert corrected["board"]["id"] != "city"
    assert db.get_profile("city")["brief"] == original_brief
    assert db.unconsumed_signals("city")["corrections"] == []

    _create_board("temporary", context="city", active=False)
    new_route = db.create_route(
        "user",
        "brief",
        "more city jackets",
        {"_routed": "new", "context": "city", "label": "City"},
        "mreq-route-2",
        "temporary",
    )
    merged = phase2_api.correct_route(
        new_route, RouteCorrectionRequest(board_id="city")
    )
    assert db.get_profile("temporary") is None
    assert merged["board"]["id"] == "city"
    assert db.unconsumed_signals("city")["corrections"][-1]["kind"] == "brief"
    assert db.corrected_routes()[0]["corrected_to"]["board_id"] == "city"


def test_pending_upload_assignment_updates_target_and_corrects_route(
    monkeypatch, tmp_path
):
    _db(monkeypatch, tmp_path)
    _create_board("active", active=True)
    _create_board("target", context="gym", active=False)
    upload_id = db.add_upload(None, "/uploads/photo.jpg", {"one_line": "lifting top"}, "mreq-read")
    route_id = db.create_route(
        "user",
        "photo",
        "lifting top",
        {"upload_id": upload_id, "context": "city", "label": "City", "board_id": "active"},
        "mreq-route",
        "active",
    )
    monkeypatch.setattr(phase2_api, "_user_profile", lambda: db.get_profile("active"))
    taste_output = taste.TasteProfile.model_validate(_profile())
    monkeypatch.setattr(
        taste,
        "call_task",
        lambda *_args: (taste_output, SimpleNamespace(request_id="mreq-update", latency_ms=1)),
    )
    response = phase2_api.assign_upload(upload_id, UploadAssignRequest(board_id="target"))
    assert response["taste"]["version"] == 2
    assert db.get_upload(upload_id)["profile_id"] == "target"
    assert not any(db.unconsumed_signals("target").values())
    assert db.get_route(route_id)["corrected"] == 1
    assert db.corrected_routes()[0]["corrected_to"]["board_id"] == "target"


def test_core_aggregates_only_across_multiple_boards(monkeypatch):
    boards = [
        {
            "id": "a",
            "label": "Gym",
            "updated_at": "2026-01-01",
            "brief": {"sizes": {"tops": "M"}},
            "taste": {
                "rules": ["No large logos", "Prefer relaxed fit"],
                "likes": {"workwear": 0.8},
                "avoids": {"logos": 0.6},
                "palette": ["Olive", "black"],
            },
        },
        {
            "id": "b",
            "label": "Outdoor",
            "updated_at": "2026-01-02",
            "brief": {"sizes": {"tops": "L"}},
            "taste": {
                "rules": ["No large logos please", "Prefer relaxed fit always"],
                "likes": {"workwear": 0.6},
                "avoids": {"logos": 0.8},
                "palette": ["olive", "cream"],
            },
        },
    ]
    monkeypatch.setattr(db, "profile_boards", lambda _kind: boards)
    core = shared_core()
    assert core["rules"]
    assert core["likes"]["workwear"] == pytest.approx(0.35)
    assert core["avoids"]["logos"] == pytest.approx(0.35)
    assert core["palette"] == ["olive"]
    assert core["sizes"] == {"tops": "L"}
    monkeypatch.setattr(db, "profile_boards", lambda _kind: boards[:1])
    assert shared_core() == {
        "rules": [],
        "likes": {},
        "avoids": {},
        "palette": [],
        "sizes": {},
    }


def test_shoe_cap_slot_round_robin_and_shoe_free_validation():
    ranked = [
        _item(f"top-{index}", "top", [f"top{index}"]) for index in range(6)
    ] + [
        _item(f"bottom-{index}", "bottom", [f"bottom{index}"]) for index in range(6)
    ] + [_item(f"shoe-{index}", "shoes") for index in range(4)]
    deck8 = make_deck(ranked, 8, _brief(pieces=["top", "bottom"]))
    deck16 = make_deck(ranked, 16, _brief(pieces=["top", "bottom"]))
    assert sum(item["tags"]["slot"] == "shoes" for item in deck8) <= 1
    assert sum(item["tags"]["slot"] == "shoes" for item in deck16) <= 2
    assert [item["tags"]["slot"] for item in deck8[:4]] == [
        "top", "bottom", "top", "bottom"
    ]
    pool = [_item("top", "top"), _item("bottom", "bottom"), _item("dress", "dress"),
            _item("shoes", "shoes")]
    shoe_outfit = {
        "title": "Look", "mood": "Calm", "piece_ids": ["top", "bottom", "shoes"],
        "why": "Complete look",
    }
    valid, _ = validate_outfits(
        [shoe_outfit], pool, 1, [], 100, include_shoes=False
    )
    assert not valid
    shoe_free = {**shoe_outfit, "piece_ids": ["top", "bottom", "dress"]}
    valid, errors = validate_outfits(
        [shoe_free], pool, 1, [], 100, include_shoes=False
    )
    assert not errors and len(valid) == 1
    brief = _brief(include_shoes=False)
    assert "Request: clothes only, no shoes." in stylist_user_content(
        _profile(), 1, [], [], include_shoes=False
    )
    assert all(
        item["tags"]["slot"] != "shoes"
        for item in build_pool(pool, brief, _profile(), [], [])
    )


@pytest.mark.parametrize(
    ("fixture", "status", "reason"),
    [
        ("sport_aggregate.html", 200, "not_single_offer"),
        ("sport_shipping.html", 200, "no_single_product"),
        ("sport_product.html", 404, "http_404"),
    ],
)
def test_sport_product_parser_fixtures(fixture, status, reason):
    record, actual_reason = parse_product_html(
        (FIXTURES / fixture).read_text(),
        "https://www.nike.com/gb/t/trail-shell/ABC123",
        status=status,
        gender="m",
    )
    assert record is None
    assert actual_reason == reason


def test_sport_product_parser_accepts_single_gbp_offer():
    record, reason = parse_product_html(
        (FIXTURES / "sport_product.html").read_text(),
        "https://www.nike.com/gb/t/stride-mens-repel-uv-running-jacket-7R8glm",
        gender="m",
        query="running jacket",
    )
    assert reason is None
    assert record["price_gbp"] == 109.99
    assert record["source"] == "nike.com"
    assert record["image_url"].endswith("M+NK+UV+RPL+STRIDE+JACKET.png")
    assert record["raw_json"]["provenance"] == "live"


def test_sport_search_does_not_refetch_cached_source_url(monkeypatch):
    url = "https://www.nike.com/gb/t/trail-shell/ABC123"

    class Tavily:
        def search(self, **_kwargs):
            return {"results": [{"url": url, "title": "Trail Shell Jacket"}]}

    monkeypatch.setattr(sport, "_tavily_client", lambda: Tavily())
    monkeypatch.setattr(
        sport.Fetcher,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached page should not be fetched")
        ),
    )
    records, report = sport.search_context(
        ["trail shell"], "gym", "m", max_searches=1, cached_urls={sport._canonical(url)}
    )
    assert records == []
    assert report["nike.com"]["cached_url"] >= 1


def test_sport_listing_harvester_filters_and_caps_links():
    listing_url = "https://www.nike.com/gb/w/mens-running-jackets"
    cached = {
        sport._canonical("https://www.nike.com/gb/t/cached-shell-CACHED01")
    }
    links, skipped = sport._harvest_product_links(
        (FIXTURES / "sport_listing.html").read_text(),
        listing_url,
        "nike.com",
        cached,
        set(),
    )
    assert len(links) == 6
    assert links[0] == "https://www.nike.com/gb/t/running-jacket-100001"
    assert links[-1] == "https://www.nike.com/gb/t/running-jacket-100006"
    assert {
        item["reason"] for item in skipped
    } == {"cached_url", "cross_domain_link", "duplicate_url", "not_product_url"}
    assert all("decathlon" not in link for link in links)


def test_sport_search_rotates_domain_pairs(monkeypatch):
    calls = []

    class Tavily:
        def search(self, **kwargs):
            calls.append(kwargs["include_domains"])
            return {"results": []}

    monkeypatch.setattr(sport, "_tavily_client", lambda: Tavily())
    records, report = sport.search_context(
        [f"query {index}" for index in range(8)],
        "gym",
        "m",
        max_searches=8,
    )
    assert records == []
    assert calls == [
        ["gymshark.com", "nike.com"],
        ["underarmour.co.uk", "lululemon.co.uk"],
        ["decathlon.co.uk", "sportsdirect.com"],
        ["gymshark.com", "nike.com"],
    ]
    assert report["candidate_diagnostics"] == []


def test_sport_search_caps_product_fetches(monkeypatch):
    html = (FIXTURES / "sport_product.html").read_bytes()
    urls = [
        f"https://www.nike.com/gb/t/running-shell-{index:06d}"
        for index in range(20)
    ]

    class Tavily:
        def search(self, **_kwargs):
            return {"results": [{"url": url} for url in urls]}

    monkeypatch.setattr(sport, "_tavily_client", lambda: Tavily())
    monkeypatch.setattr(
        sport.Fetcher,
        "get",
        lambda url, **_kwargs: SimpleNamespace(status=200, body=html, url=url),
    )
    monkeypatch.setattr(sport, "_usable_image", lambda *_args: True)
    records, report = sport.search_context(
        ["running shell"], "gym", "m", max_products=99
    )
    assert len(records) == 12
    assert report["nike.com"]["product_fetches"] == 12
    assert report["nike.com"]["max_products"] == 8


def test_sport_candidate_report_keeps_last_30(monkeypatch):
    class Tavily:
        def search(self, **_kwargs):
            return {
                "results": [
                    {"url": f"https://example.test/category/{index}"}
                    for index in range(35)
                ]
            }

    monkeypatch.setattr(sport, "_tavily_client", lambda: Tavily())
    _records, report = sport.search_context(
        ["query"], "gym", "m", max_searches=1
    )
    diagnostics = report["candidate_diagnostics"]
    assert len(diagnostics) == 30
    assert diagnostics[0]["url"].endswith("/5")
    assert diagnostics[-1]["url"].endswith("/34")
    assert all(item["reason"] == "not_allowed_domain" for item in diagnostics)


def test_context_source_selection():
    assert CONTEXT_SOURCES["gym"] == [
        "gymshark.com", "nike.com", "underarmour.co.uk", "lululemon.co.uk",
        "decathlon.co.uk", "sportsdirect.com",
    ]
    assert "vinted" not in CONTEXT_SOURCES["running"]
    assert CONTEXT_SOURCES["swim"] == [
        "decathlon.co.uk", "sportsdirect.com", "vinted", "asos.com"
    ]
    assert "patagonia.com" in CONTEXT_SOURCES["outdoor"]
    assert "adidas.co.uk" not in sum(CONTEXT_SOURCES.values(), [])
    assert CONTEXT_SOURCES["city"] == ["vinted", "asos.com", "uniqlo.com"]
