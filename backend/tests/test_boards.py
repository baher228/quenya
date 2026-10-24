from pathlib import Path
from types import SimpleNamespace

import pytest

from app import db, phase2_api, routing, taste
from app.config import CONTEXT_SOURCES
from app.core import shared_core
from app.outfits import build_pool, stylist_user_content, validate_outfits
from app.rank import make_deck
from app.schemas import (
    ProfileBrief,
    ProfileCreateRequest,
    RouteCorrectionRequest,
    RouteOut,
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
