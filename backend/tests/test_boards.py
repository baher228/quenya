import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from app import db, phase2_api, routing, taste
from app.config import CONTEXT_SOURCES
from app.core import as_profile, shared_core
from app.outfits import build_pool, stylist_user_content, validate_outfits
from app.rank import add_exploration, make_deck, rank_items
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


@pytest.fixture(autouse=True)
def stub_style_decoder(monkeypatch):
    monkeypatch.setattr(phase2_api, "decode_brief", lambda _brief: [])


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
        "bag": "bag",
        "accessory": "belt",
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
    decoded_briefs = []
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
    monkeypatch.setattr(
        phase2_api,
        "decode_brief",
        lambda brief: decoded_briefs.append(brief) or ["roadman"],
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
    assert db.get_profile("board-1")["brief"]["style_refs"] == ["roadman"]
    assert len(decoded_briefs) == 1
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
    monkeypatch.setattr(phase2_api, "decode_brief", lambda _brief: ["roadman"])
    monkeypatch.setattr(phase2_api, "_start_fetch", lambda *_args: "job-1")

    taste_contents = []

    def fake_taste_call(_task, _system, _model, content, _output_model):
        taste_contents.append(content)
        return (
            TasteProfile.model_validate(_profile()),
            SimpleNamespace(request_id="mreq-taste", latency_ms=10),
        )

    monkeypatch.setattr(
        taste,
        "call_task",
        fake_taste_call,
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
    assert db.get_profile(result["profile_id"])["brief"]["style_refs"] == ["roadman"]
    assert "Style notes (what these style names usually mean):" in taste_contents[0]
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
    monkeypatch.setattr(phase2_api, "decode_brief", lambda _brief: ["roadman"])
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
    assert db.get_profile("a")["brief"]["style_refs"] == ["roadman"]
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
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: [item])
    assert [entry["id"] for entry in phase2_api.deck(1)] == ["item-1"]


def test_tag_evidence_counts_consumed_and_unconsumed_swipes_for_one_board(
    monkeypatch, tmp_path
):
    _db(monkeypatch, tmp_path)
    db.create_profile("board-a", _brief(), active=True)
    db.create_profile("board-b", _brief(), active=False)
    kept = _item("kept", "top", ["workwear", "heritage"])
    kept["tags"].update(colours=["olive"], materials=["cotton"], fit="relaxed", era="90s")
    passed = _item("passed", "bottom", ["workwear"])
    other = _item("other", "top", ["other-board"])
    items = [kept, passed, other]
    db.insert_items(
        [
            {
                "id": item["id"],
                "source": "vinted",
                "source_url": f"https://vinted.test/{item['id']}",
                "title": item["id"],
                "price_gbp": item["price_gbp"],
                "image_url": f"https://image.test/{item['id']}.jpg",
                "raw_json": {},
            }
            for item in items
        ]
    )
    for item in items:
        db.save_tags(item["id"], item["tags"], "mreq-tag")
    db.insert_swipe("board-a", "kept", True)
    db.insert_swipe("board-a", "passed", False)
    db.insert_swipe("board-b", "other", True)
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE swipes SET consumed_version=1 WHERE profile_id='board-a' AND item_id='kept'"
        )

    evidence = db.tag_evidence("board-a")

    assert evidence["workwear"] == {"kept": 1, "passed": 1}
    assert evidence["heritage"] == {"kept": 1, "passed": 0}
    assert evidence["shirt"] == {"kept": 1, "passed": 0}
    assert evidence["trousers"] == {"kept": 0, "passed": 1}
    assert evidence["cotton"] == {"kept": 1, "passed": 0}
    assert "other-board" not in evidence


def test_swipe_history_counts_all_board_swipes_and_sorts_tags(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    db.create_profile("board-a", _brief(), active=True)
    db.create_profile("board-b", _brief(), active=False)
    items = [
        _item("kept-1", "top"),
        _item("kept-2", "outerwear"),
        _item("passed", "bottom"),
        _item("other-board", "top"),
    ]
    items[0]["tags"].update(
        category="shirt", aesthetics=["workwear", "heritage"], colours=["olive"]
    )
    items[1]["tags"].update(
        category="jacket", aesthetics=["workwear"], colours=["brown", "olive"]
    )
    items[2]["tags"].update(
        category="trousers", aesthetics=["ignored"], colours=["black"]
    )
    items[3]["tags"].update(
        category="other", aesthetics=["other-board"], colours=["purple"]
    )
    db.insert_items(
        [
            {
                    "id": item["id"],
                    "source": "vinted",
                    "source_url": f"https://vinted.test/{item['id']}",
                    "title": item["id"],
                "price_gbp": item["price_gbp"],
                "image_url": f"https://image.test/{item['id']}.jpg",
                "raw_json": {},
            }
            for item in items
        ]
    )
    for item in items:
        db.save_tags(item["id"], item["tags"], "mreq-tag")
    db.insert_swipe("board-a", "kept-1", True)
    db.insert_swipe("board-a", "kept-2", True)
    db.insert_swipe("board-a", "passed", False)
    db.insert_swipe("board-b", "other-board", True)
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE swipes SET consumed_version=1 WHERE profile_id='board-a'"
        )

    assert db.swipe_history("board-a") == {
        "kept": 2,
        "passed": 1,
        "aesthetics": [("workwear", 2), ("heritage", 1)],
        "categories": [("jacket", 1), ("shirt", 1)],
        "colours": [("olive", 2), ("brown", 1)],
    }


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


def test_shared_core_disabled_keeps_deck_and_outfit_prompt_board_local(
    monkeypatch, tmp_path
):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    brief = _brief(pieces=["top"])
    profile = _profile(
        likes={"workwear": 0.1, "minimal": 0.15},
        avoids={},
        rules=[],
        palette=[],
        niche_level=0.3,
    )
    boards = [
        {
            "id": "a",
            "label": "A",
            "updated_at": "2026-01-01",
            "brief": {},
            "taste": profile,
        },
        {
            "id": "b",
            "label": "B",
            "updated_at": "2026-01-02",
            "brief": {},
            "taste": {
                **profile,
                "likes": {"workwear": 1.0},
            },
        },
    ]
    monkeypatch.setattr(db, "profile_boards", lambda _kind: boards)
    core = shared_core()
    items = [
        _item("workwear-item", "top", ["workwear"]),
        _item("minimal-item", "top", ["minimal"]),
    ]
    local_ranking = rank_items(items, brief, profile)
    shared_ranking = rank_items(
        items, brief, profile, core_profile=as_profile(core)
    )
    local_ids = [item["id"] for item in local_ranking]
    assert local_ids == ["minimal-item", "workwear-item"]
    assert [item["id"] for item in shared_ranking] != local_ids

    shared_core_calls = []
    monkeypatch.setattr(
        phase2_api,
        "shared_core",
        lambda: shared_core_calls.append(True) or core,
    )
    monkeypatch.setattr(
        phase2_api, "_user_profile", lambda: {"id": "a", "brief": brief}
    )
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: profile)
    for item in items:
        item["source"] = "vinted"
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: items)
    route_rankings = []
    original_rank_items = phase2_api.rank_items

    def capture_ranking(*args, **kwargs):
        ranked = original_rank_items(*args, **kwargs)
        route_rankings.append(
            ([item["id"] for item in ranked], kwargs.get("core_profile"))
        )
        return ranked

    monkeypatch.setattr(phase2_api, "rank_items", capture_ranking)
    empty_core = {
        "rules": [],
        "likes": {},
        "avoids": {},
        "palette": [],
        "sizes": {},
    }
    assert phase2_api.get_core() == empty_core
    assert phase2_api.deck(1)[0]["id"] == "minimal-item"
    monkeypatch.setattr(db, "profile_boards", lambda _kind: boards[:1])
    assert phase2_api.deck(1)[0]["id"] == "minimal-item"
    assert route_rankings == [(local_ids, None), (local_ids, None)]
    assert shared_core_calls == []
    assert "Shared rules" not in stylist_user_content(
        profile, 1, [], items, core_rules=None
    )


def test_shared_core_enabled_contributes_to_deck_ranking(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", True)
    brief = _brief(pieces=["top"])
    profile = _profile(
        likes={},
        avoids={},
        rules=[],
        palette=[],
        niche_level=0.3,
    )
    core = {
        "rules": [],
        "likes": {"workwear": 1.0},
        "avoids": {},
        "palette": [],
        "sizes": {},
    }
    monkeypatch.setattr(phase2_api, "shared_core", lambda: core)
    monkeypatch.setattr(
        phase2_api,
        "_user_profile",
        lambda: {"id": "a", "brief": brief},
    )
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: profile)
    items = [
        _item("a-minimal-item", "top", ["minimal"]),
        _item("z-workwear-item", "top", ["workwear"]),
    ]
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: items)
    core_profiles = []
    original_rank_items = phase2_api.rank_items

    def capture_core_profile(*args, **kwargs):
        core_profiles.append(kwargs.get("core_profile"))
        return original_rank_items(*args, **kwargs)

    monkeypatch.setattr(phase2_api, "rank_items", capture_core_profile)

    deck = phase2_api.deck(1)

    assert core_profiles == [phase2_api.as_profile(core)]
    assert deck[0]["id"] == "z-workwear-item"


def test_shoe_cap_slot_round_robin_and_shoe_free_validation():
    ranked = [
        _item(f"top-{index}", "top", [f"top{index}"]) for index in range(6)
    ] + [
        _item(f"bottom-{index}", "bottom", [f"bottom{index}"]) for index in range(6)
    ] + [_item(f"shoe-{index}", "shoes") for index in range(4)]
    brief = _brief(looking_for="pieces", pieces=["top", "bottom"])
    deck8 = make_deck(ranked, 8, brief)
    deck16 = make_deck(ranked, 16, brief)
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
    shoe_free = {**shoe_outfit, "piece_ids": ["top", "bottom"]}
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


def test_outfits_deck_rotates_across_all_ranked_slots():
    ranked = [
        *(_item(f"outerwear-{index}", "outerwear") for index in range(12)),
        *(_item(f"bottom-{index}", "bottom") for index in range(12)),
        *(_item(f"top-{index}", "top") for index in range(12)),
    ]
    brief = _brief(
        looking_for="outfits",
        pieces=["outerwear", "knitwear", "bottom", "shoes"],
    )

    deck = make_deck(ranked, 10, brief, include_shoes=False)

    assert len(deck) == 10
    assert any(item["tags"]["slot"] == "top" for item in deck)


def test_outfits_deck_rotates_top_slot_by_layer_role():
    ranked = []
    for index in range(6):
        item = _item(f"layered-top-{index}", "top")
        item["title"] = (
            f"Ranked {index} Hoodie"
            if index < 3
            else f"Ranked {index} Sweatshirt"
        )
        ranked.append(item)
    for index in range(3):
        item = _item(f"base-top-{index}", "top")
        item["title"] = f"Ranked {index} T-Shirt"
        ranked.append(item)
    ranked.extend(_item(f"outerwear-{index}", "outerwear") for index in range(6))
    ranked.extend(_item(f"bottom-{index}", "bottom") for index in range(6))
    brief = _brief(
        looking_for="outfits",
        pieces=["outerwear", "knitwear", "bottom", "shoes"],
    )

    deck = make_deck(ranked, 10, brief, include_shoes=False)

    assert len(deck) == 10
    assert sum("T-Shirt" in item.get("title", "") for item in deck) >= 2


def test_pieces_deck_rotation_stays_with_requested_slots():
    ranked = [
        *(_item(f"outerwear-{index}", "outerwear") for index in range(12)),
        *(_item(f"bottom-{index}", "bottom") for index in range(12)),
        *(_item(f"top-{index}", "top") for index in range(12)),
    ]
    brief = _brief(looking_for="pieces", pieces=["outerwear", "bottom"])

    deck = make_deck(ranked, 10, brief, include_shoes=False)

    assert len(deck) == 10
    assert [item["tags"]["slot"] for item in deck] == ["outerwear", "bottom"] * 5


def test_deck_candidates_cap_clothing_and_accessories_preserving_order():
    clothing = [_item(f"clothing-{index}", "top") for index in range(6000)]
    for item in clothing:
        item["tags"]["condition"] = "new"
    accessories = [
        _item(
            f"accessory-{index}",
            "bag" if index % 2 else "accessory",
        )
        for index in range(800)
    ]
    items = [
        item
        for pair in zip(clothing[: len(accessories)], accessories, strict=True)
        for item in pair
    ] + clothing[len(accessories) :]

    candidates = phase2_api._deck_candidates(items)

    assert len(candidates) == 5500
    assert [item["id"] for item in candidates[:5000]] == [
        item["id"] for item in clothing[:5000]
    ]
    assert [item["id"] for item in candidates[5000:]] == [
        item["id"] for item in accessories[:500]
    ]


def test_deck_candidates_reserve_secondhand_capacity():
    new = [_item(f"new-{index}", "top") for index in range(6000)]
    for item in new:
        item["tags"]["condition"] = "new"
    secondhand = [_item(f"vinted-{index}", "top") for index in range(100)]
    for item in secondhand:
        item["source"] = "vinted"
    accessories = [
        _item(f"accessory-{index}", "bag" if index % 2 else "accessory")
        for index in range(600)
    ]
    items = [*new, *secondhand, *accessories]

    candidates = phase2_api._deck_candidates(items)
    clothing = [
        item for item in candidates
        if item["tags"]["slot"] not in {"bag", "accessory"}
    ]
    extras = [
        item for item in candidates
        if item["tags"]["slot"] in {"bag", "accessory"}
    ]

    assert len(clothing) == 5000
    assert sum(item.get("source") == "vinted" for item in clothing) == 100
    assert len(extras) == 500
    assert [item["id"] for item in clothing[:100]] == [
        item["id"] for item in secondhand
    ]


def test_deck_candidates_cap_secondhand_items_at_2000():
    new = [_item(f"new-{index}", "top") for index in range(6000)]
    for item in new:
        item["tags"]["condition"] = "new"
    secondhand = [_item(f"vinted-{index}", "top") for index in range(3000)]
    for item in secondhand:
        item["source"] = "vinted"

    candidates = phase2_api._deck_candidates([*new, *secondhand])

    assert sum(item.get("source") == "vinted" for item in candidates) == 2000
    assert sum(item["tags"]["condition"] == "new" for item in candidates) == 3000


def test_default_accessory_cap_applies_with_and_without_selector():
    ranked = []
    for index in range(20):
        item = _item(f"accessory-{index}", "top")
        item["tags"].update(slot="bag" if index % 2 else "accessory")
        ranked.append(item)
    ranked.extend(_item(f"clothing-{index}", "top") for index in range(20))
    brief = _brief(pieces=[])

    for selector in (None, lambda pool, _deck: pool[0]):
        deck = make_deck(
            ranked, 10, brief, include_shoes=False, selector=selector
        )
        assert sum(
            item["tags"]["slot"] in {"bag", "accessory"} for item in deck
        ) <= 1

    accessory_brief = _brief(looking_for="pieces", pieces=["accessory"])
    accessory_deck = make_deck(
        ranked, 10, accessory_brief, include_shoes=False
    )
    assert len(accessory_deck) == 10
    assert all(
        item["tags"]["slot"] in {"bag", "accessory"} for item in accessory_deck
    )

    belt_brief = _brief(text="navy suit with a leather belt", pieces=[])
    belt_deck = make_deck(
        ranked[:20], 10, belt_brief, include_shoes=False
    )
    assert len(belt_deck) == 10
    assert all(
        item["tags"]["slot"] in {"bag", "accessory"} for item in belt_deck
    )


def test_exploration_does_not_exceed_default_accessory_cap():
    ranked = [_item(f"clothing-{index}", "top") for index in range(20)]
    ranked.extend(
        _item(f"accessory-candidate-{index}", "accessory") for index in range(20)
    )
    deck = ranked[:10]

    result = add_exploration(
        deck, ranked, 10, _profile(), swipes=0, seed="accessory-cap"
    )

    assert sum(
        item["tags"]["slot"] in {"bag", "accessory"} for item in result
    ) <= 1


def test_add_exploration_is_deterministic_and_uses_swipe_rate():
    slots = ["top", "bottom", "outerwear"]
    ranked = [
        _item(
            f"ranked-{index:02}",
            "shoes" if index in {9, 20} else slots[index % len(slots)],
        )
        for index in range(40)
    ]
    deck = ranked[:10]

    first = add_exploration(deck, ranked, 10, _profile(), swipes=0, seed="board:0")
    repeated = add_exploration(deck, ranked, 10, _profile(), swipes=0, seed="board:0")
    later = add_exploration(deck, ranked, 10, _profile(), swipes=100, seed="board:100")

    explored = [item for item in first if item["explore"]]
    assert len(first) == len(deck) == 10
    assert [item["id"] for item in first] == [item["id"] for item in repeated]
    assert len(explored) == 4
    assert sum(item["explore"] for item in later) == 2
    assert first[9]["id"] == "ranked-09"
    assert all(item["id"] not in {entry["id"] for entry in deck} for item in explored)
    assert all(
        item["id"] in {entry["id"] for entry in ranked[20:]}
        and item["tags"]["slot"] != "shoes"
        for item in explored
    )
    assert all(not item["explore"] for item in first if item not in explored)


def test_add_exploration_samples_neutral_items_more_often_but_keeps_avoids_possible():
    ranked = [_item(f"base-{index:02}", "top") for index in range(20)]
    neutral = _item("neutral", "top", ["neutral"])
    avoided = _item("avoided", "top", ["logo"])
    fillers = [_item(f"filler-{index:02}", "top", ["filler"]) for index in range(18)]
    candidates = [neutral, avoided, *fillers]
    for item in [*candidates]:
        item["tags"]["niche"] = 0.5
    ranked.extend(candidates)
    profile = {
        "likes": {},
        "avoids": {"logo": 1.0},
        "niche_level": 0.5,
        "rules": [],
    }
    neutral_picks = 0
    avoided_picks = 0
    for seed in range(300):
        result = add_exploration(
            ranked[:10], ranked, 10, profile, swipes=0, seed=f"board:{seed}"
        )
        explored_ids = {
            item["id"] for item in result if item["explore"]
        }
        neutral_picks += "neutral" in explored_ids
        avoided_picks += "avoided" in explored_ids

    assert neutral_picks > avoided_picks > 0


def test_deck_endpoint_marks_exploration_and_keeps_budget_and_unique_ids(
    monkeypatch, tmp_path
):
    _db(monkeypatch, tmp_path)
    brief = _brief(budget_gbp=100)
    db.create_profile("board", brief, active=True)
    profile = _profile(
        likes={},
        avoids={},
        price_ceiling_gbp=100,
        niche_level=0.4,
    )
    slots = ["top", "bottom", "outerwear"]
    items = []
    for index in range(40):
        item = _item(f"deck-item-{index:02}", slots[index % len(slots)])
        item.update(
            title=f"Test product {index}",
            source_url=f"https://shop.example/items/{index}",
            image_url=f"https://images.example/{index}.jpg",
        )
        item["price_gbp"] = 20
        item["tags"]["niche"] = 0.4
        items.append(item)
    over_budget = _item("budget-excluded", "top")
    over_budget.update(
        title="Over budget product",
        source_url="https://shop.example/items/over-budget",
        image_url="https://images.example/over-budget.jpg",
    )
    over_budget["price_gbp"] = 61
    over_budget["tags"]["niche"] = 0.4
    items.append(over_budget)
    monkeypatch.setattr(
        phase2_api, "_user_profile", lambda: {"id": "board", "brief": brief}
    )
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: profile)
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: items)

    result = phase2_api.deck(10)

    assert len(result) == 10
    assert sum(item["explore"] for item in result) == 4
    assert len({item["id"] for item in result}) == len(result)
    assert "budget-excluded" not in {item["id"] for item in result}
    assert all(item["price_gbp"] <= 60 for item in result)
    assert all(
        int(item["id"].rsplit("-", maxsplit=1)[-1]) >= 20
        for item in result
        if item["explore"]
    )


def test_embed_deck_uses_targeted_vectors_without_full_embedding_load(
    monkeypatch, tmp_path
):
    _db(monkeypatch, tmp_path)
    brief = _brief(budget_gbp=100)
    db.create_profile("board", brief, active=True)
    profile = _profile(price_ceiling_gbp=100)
    item = _item("embedded-item", "top")
    item["price_gbp"] = 20
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    db.store_item_embedding("embedded-item", "clip32", None, None, vector)
    db.store_profile_embedding("board", "clip32", vector, "brief-hash")
    monkeypatch.setenv("QUENYA_RANKER", "embed")
    monkeypatch.setattr(
        phase2_api, "_user_profile", lambda: {"id": "board", "brief": brief}
    )
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: profile)
    monkeypatch.setattr(phase2_api, "_core", lambda: {"rules": []})
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: [item])
    monkeypatch.setattr(
        phase2_api, "catalogue_sample",
        lambda: np.asarray([vector, -vector], dtype=np.float32),
    )
    monkeypatch.setattr(
        phase2_api.db,
        "item_embedding_map",
        lambda: (_ for _ in ()).throw(AssertionError("full embedding load used")),
    )
    vector_queries = []
    item_mix_vectors = db.item_mix_vectors

    def capture_vectors(item_ids):
        vector_queries.append(list(item_ids))
        return item_mix_vectors(item_ids)

    monkeypatch.setattr(phase2_api.db, "item_mix_vectors", capture_vectors)

    result = phase2_api.deck(1)

    assert [entry["id"] for entry in result] == ["embedded-item"]
    assert vector_queries == [["embedded-item"]]


def test_embed_deck_searches_past_the_initial_vector_window(
    monkeypatch, tmp_path
):
    _db(monkeypatch, tmp_path)
    brief = _brief(budget_gbp=100)
    db.create_profile("board", brief, active=True, ranker_arm="embed")
    profile = _profile(price_ceiling_gbp=100)
    items = []
    for index in range(901):
        item = _item(f"embedded-{index:03d}", "top")
        item.update(
            title=f"Unique product {index}",
            source_url=f"https://shop.example/items/{index}",
            image_url=f"https://images.example/{index}.jpg",
        )
        items.append(item)
    target = items[900]
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    vector_queries = []

    def item_mix_vectors(item_ids):
        item_ids = list(item_ids)
        vector_queries.append(item_ids)
        if target["id"] in item_ids:
            return {target["id"]: {"mix": vector}}
        return {}

    def rank_candidates(
        ranked, _profile_id, _taste, _brief_vector, _swipes, _embeddings,
        _sample, *, nearest_ids,
    ):
        assert nearest_ids == [target["id"]]
        return [target], {target["id"]: 1.0}, None

    monkeypatch.setenv("QUENYA_RANKER", "embed")
    monkeypatch.setattr(
        phase2_api, "_user_profile", lambda: {"id": "board", "brief": brief}
    )
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: profile)
    monkeypatch.setattr(phase2_api, "_core", lambda: {"rules": []})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "rank_items", lambda *_args, **_kwargs: items)
    monkeypatch.setattr(phase2_api, "rank_candidates", rank_candidates)
    monkeypatch.setattr(phase2_api, "make_deck", lambda ranked, n, *_a, **_kw: ranked[:n])
    monkeypatch.setattr(phase2_api, "history_query", lambda *_args: None)
    monkeypatch.setattr(phase2_api, "catalogue_sample", lambda: np.asarray([vector]))
    monkeypatch.setattr(phase2_api, "secondhand_share", lambda *_args: 0.0)
    monkeypatch.setattr(db, "live_catalogue", lambda: items)
    monkeypatch.setattr(db, "profile_embedding", lambda _profile_id: vector)
    monkeypatch.setattr(db, "recent_deck_item_ids", lambda _profile_id: ["recent-item"])
    monkeypatch.setattr(db, "item_mix_vectors", item_mix_vectors)

    result = phase2_api.deck(1)

    assert [item["id"] for item in result] == [target["id"]]
    assert "recent-item" in vector_queries[0]
    assert target["id"] not in vector_queries[0]
    assert target["id"] in vector_queries[1]


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

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("cached page should not be fetched")

    monkeypatch.setattr(
        sport,
        "_scrapling_fetcher",
        lambda: SimpleNamespace(get=fail_fetch),
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

    def fake_fetch(url, **_kwargs):
        return SimpleNamespace(status=200, body=html, url=url)

    monkeypatch.setattr(
        sport,
        "_scrapling_fetcher",
        lambda: SimpleNamespace(get=fake_fetch),
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
    assert CONTEXT_SOURCES["city"] == [
        "vinted", "asos.com", "uniqlo.com", "arket.com", "mango.com",
        "urbanoutfitters.com", "ssense.com",
    ]
    assert CONTEXT_SOURCES["smart"] == [
        "vinted", "ssense.com", "arket.com", "mango.com", "asos.com",
    ]
