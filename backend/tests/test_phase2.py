import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import db, phase2_api, taste, warmup
from app.outfits import validate_outfits
from app.rank import rank_items
from app.schemas import (
    CheckoutRequest,
    Outfit,
    OutfitRequest,
    StylistOutput,
    SwapRequest,
    SwipeRequest,
    TasteProfile,
    TasteUpdateRequest,
    VerdictRequest,
)


def _brief(**overrides):
    return {
        "text": "Relaxed workwear with muted colours",
        "looking_for": "outfits",
        "pieces": [],
        "gender": "both",
        "condition": "both",
        "niche": 0.5,
        "budget_gbp": 200.0,
        "sizes": {},
        **overrides,
    }


def _taste_profile(**overrides):
    return {
        "summary": "Relaxed casual clothing",
        "likes": {"casual": 0.8},
        "avoids": {},
        "rules": [],
        "palette": ["olive", "cream"],
        "niche_level": 0.5,
        "price_ceiling_gbp": 200.0,
        "condition_preference": "both",
        "search_queries": ["relaxed workwear"],
        "changes": [],
        **overrides,
    }


def test_taste_user_content_lists_top_tag_evidence():
    evidence = {
        f"tag-{index:02}": {"kept": 30 - index, "passed": 0}
        for index in range(24)
    }
    evidence.update(
        {
            "zeta": {"kept": 1, "passed": 1},
            "alpha": {"kept": 1, "passed": 1},
            "ignored": {"kept": 1, "passed": 0},
        }
    )

    content = taste.taste_user_content(_brief(), None, 1, [], evidence=evidence)
    section = content.split("Evidence so far", maxsplit=1)[1]

    assert "tag-00 30/0" in section
    assert section.endswith("alpha 1/1")
    assert "zeta 1/1" not in section
    assert "ignored 1/0" not in section


def test_signal_lines_expose_non_swipe_signals_as_explicit_text():
    signals = {
        "swipes": [
            {
                "id": "swipe",
                "liked": False,
                "title": "Merino jumper",
                "price_gbp": 50,
                "tags": {"category": "jumper", "aesthetics": [], "colours": []},
            }
        ],
        "outfits": [
            {
                "id": "outfit",
                "title": "Quiet outfit",
                "verdict": "reject",
                "reason": "No logos",
                "piece_ids_json": "[]",
            }
        ],
        "corrections": [
            {"id": "brief", "kind": "brief", "note": "No logos"},
            {
                "id": "tag",
                "kind": "tag",
                "target_id": "item",
                "title": "Logo jumper",
                "note": "Remove the logo",
            },
        ],
        "uploads": [
            {
                "id": "upload",
                "read": {
                    "one_line": "Minimal, no logos",
                    "aesthetics": ["minimal"],
                    "palette": ["black"],
                },
            }
        ],
    }

    lines, consumed, explicit_lines = taste._signal_lines(signals)

    assert lines[0].startswith("- passed:")
    assert explicit_lines == lines[1:]
    assert any('reason: "No logos"' in line for line in explicit_lines)
    assert any("Remove the logo" in line for line in explicit_lines)
    assert any("Minimal, no logos" in line for line in explicit_lines)
    assert consumed == {
        "swipes": ["swipe"],
        "outfits": ["outfit"],
        "corrections": ["brief", "tag"],
        "uploads": ["upload"],
    }


def test_settle_profile_gates_new_likes_and_avoids_and_filters_changes():
    previous = _taste_profile(likes={}, avoids={})

    one_pass, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={"quiet luxury": 0.9}),
        previous,
        {"quiet luxury": {"kept": 0, "passed": 1}},
        "",
    )
    assert one_pass["avoids"] == {}
    assert blocked == {"quiet luxury"}

    two_passes, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={"quiet luxury": 0.9}),
        previous,
        {"quiet luxury": {"kept": 5, "passed": 15}},
        "",
        base_rate=0.45,
    )
    assert two_passes["avoids"] == {"quiet luxury": 0.5}
    assert blocked == set()

    equal_passes, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={"quiet luxury": 0.9}),
        previous,
        {"quiet luxury": {"kept": 2, "passed": 2}},
        "",
    )
    assert equal_passes["avoids"] == {}
    assert blocked == {"quiet luxury"}

    one_keep, blocked = taste.settle_profile(
        _taste_profile(
            likes={"quiet luxury": 0.9},
            changes=["Added quiet luxury after one kept jumper", "Keep workwear"],
        ),
        previous,
        {"quiet luxury": {"kept": 1, "passed": 0}},
        "",
    )
    assert one_keep["likes"] == {}
    assert one_keep["changes"] == ["Keep workwear"]
    assert blocked == {"quiet luxury"}

    two_keeps, blocked = taste.settle_profile(
        _taste_profile(likes={"aesthetic:workwear": 0.9}),
        previous,
        {"workwear": {"kept": 2, "passed": 0}},
        "",
        base_rate=0.2,
    )
    assert two_keeps["likes"] == {"workwear": 0.5}
    assert blocked == set()

    unseen, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={"logo": 0.9}), previous, {}, ""
    )
    assert unseen["avoids"] == {}
    assert blocked == {"logo"}


def test_settle_profile_steps_existing_weights_and_decays_dropped_keys():
    previous = _taste_profile(
        likes={"style:workwear": 0.9, "heritage": 0.5, "quiet": 0.15},
        avoids={},
    )
    new = _taste_profile(likes={"aesthetic:workwear": 0.1}, avoids={})

    settled, blocked = taste.settle_profile(new, previous, {}, "")

    assert settled["likes"]["workwear"] == pytest.approx(0.1)
    assert settled["likes"]["heritage"] == pytest.approx(0.2)
    assert "quiet" not in settled["likes"]
    assert blocked == {"quiet"}


def test_settle_profile_drops_gender_filter_keys_from_existing_profiles():
    profile = _taste_profile(likes={"gender:both": 1.0}, avoids={})

    settled, _blocked = taste.settle_profile(profile, profile, {}, "")

    assert settled["likes"] == {}


def test_settle_profile_explicit_text_bypasses_gates_and_allows_removal():
    new = _taste_profile(likes={}, avoids={"logos": 0.9})
    settled, blocked = taste.settle_profile(
        new,
        _taste_profile(likes={}, avoids={}),
        {},
        "Correction: NO LOGOS",
        brief_text="",
    )
    assert settled["avoids"] == {"logos": 0.9}
    assert blocked == set()

    removed, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={}),
        _taste_profile(likes={}, avoids={"logos": 0.7}),
        {},
        "Correction: no logos",
        brief_text="",
    )
    assert removed["avoids"] == {}
    assert blocked == {"logos"}


def test_normalised_prefixes_and_word_boundaries_match_evidence():
    assert taste._normalised_key("color_dark_grey") == "dark grey"
    assert taste._normalised_key("brands:stone island") == "stone island"
    previous = _taste_profile(likes={}, avoids={})

    dark_grey, blocked = taste.settle_profile(
        _taste_profile(likes={"color_dark_grey": 0.9}),
        previous,
        {"dark grey": {"kept": 1, "passed": 0}},
        "",
    )
    assert dark_grey["likes"] == {}
    assert blocked == {"dark grey"}

    red, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={"red": 0.9}, changes=["Tailored trousers"]),
        previous,
        {"tailored trousers": {"kept": 0, "passed": 1}},
        "",
    )
    assert red["avoids"] == {}
    assert red["changes"] == ["Tailored trousers"]
    assert blocked == {"red"}

    boundary, blocked = taste.settle_profile(
        _taste_profile(
            likes={},
            avoids={"red": 0.9},
            changes=["Tailored trousers", "Red boots passed"],
        ),
        previous,
        {"red": {"kept": 0, "passed": 1}},
        "Tailored trousers",
    )
    assert boundary["avoids"] == {}
    assert boundary["changes"] == ["Tailored trousers"]
    assert blocked == {"red"}


def test_brief_mention_skips_new_key_gate_but_not_caps_or_existing_step():
    previous = _taste_profile(likes={}, avoids={})
    new_avoid, blocked = taste.settle_profile(
        _taste_profile(likes={}, avoids={"quiet luxury": 0.9}),
        previous,
        {},
        "",
        brief_text="Avoid quiet luxury",
    )
    assert new_avoid["avoids"] == {"quiet luxury": 0.5}
    assert blocked == set()

    existing_like, blocked = taste.settle_profile(
        _taste_profile(likes={"style_vintage": 0.1}, avoids={}),
        _taste_profile(likes={"style_vintage": 0.9}, avoids={}),
        {},
        "",
        brief_text="Style vintage",
    )
    assert existing_like["likes"]["vintage"] == pytest.approx(0.6)
    assert blocked == set()


def test_settle_profile_returns_new_unchanged_without_previous():
    new = _taste_profile(likes={"untested": 0.9}, changes=["Keep it"])

    settled, blocked = taste.settle_profile(new, None, {}, "")

    assert settled["likes"] == {"untested": 0.9}
    assert settled is not new
    assert blocked == set()


def _item(item_id, slot, *, price=20.0, gender="unisex", condition="second-hand"):
    categories = {
        "top": "shirt",
        "bottom": "trousers",
        "shoes": "boots",
        "outerwear": "chore jacket",
    }
    return {
        "id": item_id,
        "source": "vinted",
        "source_url": f"https://www.vinted.co.uk/items/{item_id}",
        "title": f"{categories[slot]} {item_id}",
        "brand": "Example",
        "price_gbp": price,
        "image_url": f"https://images.example/{item_id}.jpg",
        "gender_hint": "u",
        "condition_hint": condition,
        "query": "test",
        "tags": {
            "slot": slot,
            "category": categories[slot],
            "colours": ["olive"],
            "materials": ["cotton"],
            "fit": "relaxed",
            "era": "contemporary",
            "aesthetics": ["casual", "japanese workwear"],
            "formality": 2,
            "niche": 0.5,
            "gender": gender,
            "logo_heavy": False,
            "condition": condition,
            "one_line": "Relaxed everyday clothing.",
        },
    }


def test_ranking_applies_preferences_and_hard_filters():
    brief = _brief(
        gender="menswear",
        looking_for="pieces",
        pieces=["outerwear"],
        budget_gbp=100,
    )
    profile = _taste_profile(
        price_ceiling_gbp=100,
        likes={"japanese workwear": 1.0},
        avoids={"large logos": 0.9},
        rules=["no large logos"],
    )
    liked = _item("liked", "outerwear", price=40, gender="menswear")
    logo = _item("logo", "outerwear", price=40, gender="menswear")
    logo["tags"]["aesthetics"] = ["large logos"]
    logo["tags"]["logo_heavy"] = True
    items = [
        liked,
        _item("too-expensive", "outerwear", price=61, gender="menswear"),
        _item("wrong-gender", "outerwear", price=30, gender="womenswear"),
        _item("wrong-slot", "top", price=30, gender="menswear"),
        logo,
    ]

    ranked = rank_items(items, brief, profile)
    assert [item["id"] for item in ranked] == ["liked", "logo"]
    assert [item["id"] for item in rank_items(items, brief, profile, exclude_ids={"liked"})] == [
        "logo"
    ]


def test_stylist_repair_retries_once_with_original_and_invalid_reply(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "outfits.sqlite")
    db.init_db()
    brief = _brief()
    profile = _taste_profile(changes=["Recent swipe: statement shoes"])
    items = [_item("top", "top"), _item("bottom", "bottom"), _item("shoes", "shoes")]
    replacement_shoes = {
        **items[2],
        "id": "replacement-shoes",
        "title": "replacement boots",
        "source_url": "https://www.vinted.co.uk/items/replacement-shoes",
        "image_url": "https://images.example/replacement-shoes.jpg",
    }
    other_board_item = _item("other-board-item", "outerwear")
    items[0]["tags"].update(
        category="shirt", aesthetics=["workwear", "heritage"], colours=["olive"]
    )
    items[1]["tags"].update(
        category="trousers", aesthetics=["workwear"], colours=["brown", "olive"]
    )
    items[2]["tags"].update(
        category="boots", aesthetics=["ignored"], colours=["black"]
    )
    other_board_item["tags"].update(
        category="parka", aesthetics=["other-board-only"], colours=["purple"]
    )
    db.create_profile("profile-1", brief)
    db.create_profile("other-board", brief, active=False)
    history_items = [*items, other_board_item]
    db.insert_items(
        [
            {key: value for key, value in item.items() if key != "tags"}
            for item in history_items
        ]
    )
    for item in history_items:
        db.save_tags(item["id"], item["tags"], "mreq-tag")
    db.insert_swipe("profile-1", "top", True)
    db.insert_swipe("profile-1", "bottom", True)
    db.insert_swipe("profile-1", "shoes", False)
    db.insert_swipe("other-board", "other-board-item", True)
    monkeypatch.setattr(
        phase2_api.db,
        "active_profile",
        lambda _kind: {"id": "profile-1", "brief": brief},
    )
    monkeypatch.setattr(
        phase2_api.db,
        "latest_taste",
        lambda _profile_id: {"version": 1, "profile": profile},
    )
    monkeypatch.setattr(
        phase2_api.db, "live_catalogue", lambda: [items[0], items[1], replacement_shoes]
    )
    monkeypatch.setattr(phase2_api.db, "save_outfit", lambda *_args, **_kwargs: "outfit-1")
    calls = []

    def fake_call_task(task, system, model, content, out_model):
        calls.append((task, system, model, content, out_model))
        invalid = len(calls) == 1
        result = StylistOutput(
            outfits=[
                Outfit(
                    title="Workwear",
                    mood="Relaxed",
                    piece_ids=["outside-pool"]
                    if invalid
                    else ["top", "bottom", "replacement-shoes"],
                    why="Balanced casual pieces.",
                )
            ]
        )
        return result, SimpleNamespace(request_id=f"mreq-{len(calls)}")

    monkeypatch.setattr(phase2_api, "call_task", fake_call_task)
    response = phase2_api.outfits(OutfitRequest(count=1))

    assert len(calls) == 2
    assert calls[0][3] == calls[1][3][0]["content"]
    assert calls[1][3][1]["role"] == "assistant"
    assert calls[1][3][2]["role"] == "user"
    stylist_profile = json.loads(calls[0][3].splitlines()[1])
    assert "changes" not in stylist_profile
    assert profile["changes"] == ["Recent swipe: statement shoes"]
    assert (
        "Overall history for this style (all swipes): kept 2, passed 1. "
        "Most kept: aesthetics workwear 2, heritage 1; "
        "categories shirt 1, trousers 1; colours olive 2, brown 1"
    ) in calls[0][3]
    assert "other-board-only" not in calls[0][3]
    assert response["request_ids"] == ["mreq-1", "mreq-2"]
    assert [piece["id"] for piece in response["outfits"][0]["pieces"]] == [
        "top",
        "bottom",
        "replacement-shoes",
    ]
    assert response["dropped"] == 0


def test_outfit_validator_rejects_missing_shoes_and_reuse():
    pool = [_item("top", "top"), _item("bottom", "bottom"), _item("shoes", "shoes")]
    outfit = {
        "title": "Workwear",
        "mood": "Relaxed",
        "piece_ids": ["top", "bottom"],
        "why": "A complete look.",
    }
    valid, errors = validate_outfits([outfit], pool, 1, [], 100)
    assert not valid
    assert any("needs exactly one pair of shoes" in error for error in errors)


def test_outfit_validator_allows_optional_accessories_with_core_piece_limits():
    def accessory(item_id, slot):
        item = _item(item_id, "top")
        item["tags"].update(
            slot=slot,
            category="bag" if slot == "bag" else "belt",
        )
        return item

    core = [_item("top", "top"), _item("bottom", "bottom"), _item("shoes", "shoes")]
    extras = [
        accessory("bag", "bag"),
        accessory("accessory-1", "accessory"),
        accessory("accessory-2", "accessory"),
    ]

    def validate(piece_ids, pieces, locked_ids=None):
        outfit = {
            "title": "Look",
            "mood": "Relaxed",
            "piece_ids": piece_ids,
            "why": "A complete outfit.",
        }
        return validate_outfits([outfit], pieces, 1, locked_ids or [], 500)

    for chosen in ([], extras[:1], extras[:2]):
        selected = core + chosen
        valid, errors = validate([item["id"] for item in selected], selected)
        assert len(valid) == 1 and not errors

    too_many = core + [accessory(f"extra-{index}", "accessory") for index in range(3)]
    valid, errors = validate([item["id"] for item in too_many], too_many)
    assert not valid
    assert any("more than two accessories" in error for error in errors)


def test_taste_update_clamps_weights_versions_and_consumes_signals(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "taste.sqlite")
    brief = _brief(condition="second-hand", budget_gbp=100)
    db.create_profile("profile-1", brief)
    initial = TasteProfile.model_validate(_taste_profile())
    db.save_taste_version("profile-1", 1, initial.model_dump(), {}, "mreq-v1")
    item = _item("signal-item", "outerwear", price=25)
    db.insert_items([{key: value for key, value in item.items() if key != "tags"}])
    db.save_tags(item["id"], item["tags"], "mreq-tag")
    db.insert_swipe("profile-1", item["id"], False)
    weights = {f"like-{index}": 2.0 for index in range(17)}
    output = TasteProfile.model_validate(
        _taste_profile(
            likes=weights,
            avoids={"avoid": -0.5, "chore jacket": 0.9},
            price_ceiling_gbp=0,
            condition_preference="new",
            changes=["Added avoidance of chore jacket after one pass"],
        )
    )
    prompts = []

    def fake_call_task(_task, _system, _model, user_content, _out_model):
        prompts.append(user_content)
        return output, SimpleNamespace(request_id="mreq-v2", latency_ms=5)

    monkeypatch.setattr(taste, "call_task", fake_call_task)
    version, profile, _meta, _signals = taste.update_taste(db.get_profile("profile-1"))

    assert version == 2
    assert profile["price_ceiling_gbp"] == 100
    assert profile["condition_preference"] == "second-hand"
    assert profile["likes"] == {"casual": 0.5}
    assert profile["avoids"] == {}
    assert profile["changes"] == []
    assert "Current profile (v1):" in prompts[0]
    assert "New signals:" in prompts[0]
    assert "Evidence so far" in prompts[0]
    assert "chore jacket" not in db.latest_taste("profile-1")["profile"]["avoids"]
    assert not any(db.unconsumed_signals("profile-1").values())
    with pytest.raises(ValueError, match="No unconsumed taste signals"):
        taste.update_taste(db.get_profile("profile-1"))


def test_checkout_is_explicitly_simulated(monkeypatch):
    outfit = {
        "id": "outfit-1",
        "profile_id": "profile-1",
        "piece_ids": ["item-1", "item-2"],
        "total_gbp": 42.5,
    }
    items = {
        "item-1": {
            "title": "Canvas jacket",
            "source": "vinted",
            "source_url": "https://www.vinted.co.uk/items/1",
            "price_gbp": 30.0,
        },
        "item-2": {
            "title": "Canvas shoes",
            "source": "retail",
            "source_url": "https://www.asos.com/prd/2",
            "price_gbp": 12.5,
        },
    }
    monkeypatch.setattr(
        phase2_api.db,
        "active_profile",
        lambda _kind: {"id": "profile-1", "brief": _brief()},
    )
    monkeypatch.setattr(
        phase2_api.db,
        "get_profile",
        lambda profile_id, kind=None: {
            "id": "profile-1",
            "owner_id": "owner-1",
            "brief": _brief(),
        } if profile_id == "profile-1" and kind == "user" else None,
    )
    monkeypatch.setattr(phase2_api.db, "get_outfit", lambda _outfit_id: outfit)
    monkeypatch.setattr(phase2_api.db, "get_item", lambda item_id: items[item_id])
    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-1")

    result = phase2_api.checkout(
        CheckoutRequest(outfit_id="outfit-1", board_id="profile-1")
    )

    assert result["simulated"] is True
    assert result["total_gbp"] == 42.5
    assert result["outfit_id"] == "outfit-1"
    assert len(result["lines"]) == 2
    assert result["order_ref"].startswith("DEMO-")


def test_outfits_board_id_uses_owned_non_active_profile(monkeypatch):
    board = {"id": "board-b", "owner_id": "owner-1", "brief": _brief()}
    taste_profile = _taste_profile()
    pool = [_item("item-1", "top")]
    saved_profile_ids = []
    taste_profile_ids = []

    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-1")
    monkeypatch.setattr(
        db,
        "get_profile",
        lambda profile_id, kind=None: board
        if profile_id == "board-b" and kind == "user"
        else None,
    )
    monkeypatch.setattr(
        db,
        "active_profile",
        lambda _kind: {"id": "board-a", "brief": _brief()},
    )
    monkeypatch.setattr(
        db,
        "latest_taste",
        lambda profile_id: (
            taste_profile_ids.append(profile_id)
            or {"version": 1, "profile": taste_profile}
        ),
    )
    monkeypatch.setattr(db, "live_catalogue", lambda: pool)
    monkeypatch.setattr(db, "profile_swipe_items", lambda _profile_id: [])
    monkeypatch.setattr(db, "swipe_history", lambda _profile_id: {})
    monkeypatch.setattr(
        phase2_api,
        "build_pool",
        lambda *_args, **_kwargs: pool,
    )
    monkeypatch.setattr(
        phase2_api,
        "stylist_user_content",
        lambda *_args, **_kwargs: "prompt",
    )
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(
        phase2_api,
        "call_task",
        lambda *_args: (StylistOutput(outfits=[]), SimpleNamespace(request_id="req-1")),
    )
    monkeypatch.setattr(
        phase2_api,
        "validate_outfits",
        lambda *_args, **_kwargs: (
            [
                {
                    "title": "Workwear",
                    "mood": "Relaxed",
                    "why": "Balanced pieces.",
                    "piece_ids": [],
                    "total_gbp": 30,
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(phase2_api, "order_outfit_pieces", lambda pieces: pieces)
    monkeypatch.setattr(
        db,
        "save_outfit",
        lambda profile_id, *_args, **_kwargs: (
            saved_profile_ids.append(profile_id) or "outfit-b"
        ),
    )

    result = phase2_api.outfits(OutfitRequest(count=1, board_id="board-b"))

    assert result["outfits"][0]["id"] == "outfit-b"
    assert saved_profile_ids == ["board-b"]
    assert taste_profile_ids == ["board-b"]
    assert db.active_profile("user")["id"] == "board-a"


def test_foreign_board_id_is_not_found_for_outfit_mutations(monkeypatch):
    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-1")
    monkeypatch.setattr(
        db,
        "get_profile",
        lambda profile_id, kind=None: {
            "id": profile_id,
            "owner_id": "owner-2",
            "brief": _brief(),
        }
        if profile_id == "foreign" and kind == "user"
        else None,
    )
    monkeypatch.setattr(
        db,
        "get_outfit",
        lambda _outfit_id: pytest.fail("outfit lookup must follow board ownership"),
    )

    requests = (
        lambda: phase2_api.outfits(OutfitRequest(board_id="foreign")),
        lambda: phase2_api.swap(
            "outfit-1", SwapRequest(item_id="item-1", board_id="foreign")
        ),
        lambda: phase2_api.verdict(
            "outfit-1", VerdictRequest(verdict="like", board_id="foreign")
        ),
    )
    for request in requests:
        with pytest.raises(HTTPException) as exc:
            request()
        assert exc.value.status_code == 404
        assert exc.value.detail == "Board not found"


def _create_board_pair(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "board-id.sqlite")
    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-1")
    db.create_profile("board-a", _brief(), active=True, owner_id="owner-1")
    db.create_profile("board-b", _brief(), active=False, owner_id="owner-1")
    for board_id in ("board-a", "board-b"):
        db.save_taste_version(
            board_id,
            1,
            TasteProfile.model_validate(_taste_profile()).model_dump(),
            {},
            f"taste-{board_id}",
        )


def _insert_tagged_items(*items):
    for item in items:
        db.insert_items([{key: value for key, value in item.items() if key != "tags"}])
        db.save_tags(item["id"], item["tags"], "tag-test")


def test_swipe_board_id_uses_owned_non_active_profile(monkeypatch, tmp_path):
    _create_board_pair(monkeypatch, tmp_path)
    items = [_item(f"target-item-{index}", "outerwear") for index in range(10)]
    _insert_tagged_items(*items)

    for index, item in enumerate(items, start=1):
        result = phase2_api.swipe(
            SwipeRequest(item_id=item["id"], liked=True, board_id="board-b")
        )
        assert result["swipes_since_update"] == index
        assert result["update_due"] is (index == 10)

    assert db.profile_swipes("board-a") == []
    assert len(db.profile_swipes("board-b")) == 10
    assert db.active_profile("user", owner_id="owner-1")["id"] == "board-a"


def test_swipe_without_board_id_uses_active_profile(monkeypatch, tmp_path):
    _create_board_pair(monkeypatch, tmp_path)
    _insert_tagged_items(_item("active-item", "outerwear"))

    phase2_api.swipe(SwipeRequest(item_id="active-item", liked=False))

    assert db.profile_swipes("board-a") == [
        {"item_id": "active-item", "liked": False}
    ]
    assert db.profile_swipes("board-b") == []


def test_deck_board_id_filters_swipes_and_records_events_for_target(
    monkeypatch, tmp_path
):
    _create_board_pair(monkeypatch, tmp_path)
    item_a = _item("item-a", "outerwear")
    item_b = _item("item-b", "top")
    _insert_tagged_items(item_a, item_b)
    db.insert_swipe("board-a", "item-b", True)
    db.insert_swipe("board-b", "item-a", True)
    monkeypatch.setenv("QUENYA_RANKER", "tags")
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "secondhand_share", lambda *_args: 0.0)
    monkeypatch.setattr(db, "live_catalogue", lambda: [item_a, item_b])
    monkeypatch.setattr(db, "profile_live_items", lambda _profile_id: [])

    targeted = phase2_api.deck(n=10, board_id="board-b")
    active = phase2_api.deck(n=10)

    assert [item["id"] for item in targeted] == ["item-b"]
    assert [item["id"] for item in active] == ["item-a"]
    assert db.recent_deck_item_ids("board-b") == ["item-b"]
    assert db.recent_deck_item_ids("board-a") == ["item-a"]


def test_deck_hides_disliked_product_only_from_its_own_profile(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "lookalikes.sqlite")
    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-a")
    profile = TasteProfile.model_validate(_taste_profile()).model_dump()
    db.create_profile("board-a", _brief(), active=True, owner_id="owner-a")
    db.save_taste_version("board-a", 1, profile, {}, "taste-a")
    db.create_profile("board-b", _brief(), active=True, owner_id="owner-b")
    db.save_taste_version("board-b", 1, profile, {}, "taste-b")

    swiped = _item("swiped", "outerwear")
    swiped["title"] = "Dickies Cargo Trousers - Navy"
    swiped["source_url"] = "https://www.vinted.co.uk/items/swiped"
    lookalike = _item("lookalike", "outerwear")
    lookalike["title"] = "Dickies Cargo Trousers - Green"
    lookalike["source_url"] = "https://www.vinted.co.uk/items/lookalike"
    other = _item("other", "outerwear")
    other["title"] = "Different Workwear Jacket"
    _insert_tagged_items(swiped, lookalike, other)
    db.insert_swipe("board-a", "swiped", False)
    db.insert_swipe("board-a", "swiped", True)
    db.insert_swipe("board-a", "swiped", False)
    monkeypatch.setenv("QUENYA_RANKER", "tags")
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "secondhand_share", lambda *_args: 0.0)
    monkeypatch.setattr(db, "live_catalogue", lambda: [lookalike, other])
    monkeypatch.setattr(db, "profile_live_items", lambda _profile_id: [])

    board_a = phase2_api.deck(n=10, board_id="board-a")
    assert "lookalike" not in {item["id"] for item in board_a}
    assert db.profile_swipe_items("board-a")[-1][1] is False

    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-b")
    board_b = phase2_api.deck(n=10, board_id="board-b")
    assert "lookalike" in {item["id"] for item in board_b}


def test_outfits_exclude_disliked_items_and_lookalikes_but_keep_likes(
    monkeypatch, tmp_path
):
    _create_board_pair(monkeypatch, tmp_path)
    liked = _item("liked", "outerwear")
    liked["title"] = "Liked Leather Jacket"
    disliked = _item("disliked", "outerwear")
    disliked["title"] = "Disliked Cargo Trousers - Navy"
    product_lookalike = _item("product-lookalike", "outerwear")
    product_lookalike["title"] = "Disliked Cargo Trousers - Green"
    image_lookalike = _item(
        "image-lookalike",
        "outerwear",
        price=30,
    )
    image_lookalike["title"] = "Different Product"
    image_lookalike["image_url"] = disliked["image_url"] + "?variant=large"
    other = _item("other", "outerwear")
    other["title"] = "Other Jacket"
    _insert_tagged_items(
        liked, disliked, product_lookalike, image_lookalike, other
    )
    db.insert_swipe("board-a", "liked", True)
    db.insert_swipe("board-a", "disliked", False)
    seen = {}

    def capture_pool(items, _brief_arg, _profile, _locked, exclude_ids, **_kwargs):
        seen["ids"] = {item["id"] for item in items}
        seen["exclude_ids"] = set(exclude_ids)
        return []

    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "_core", lambda: {"rules": []})
    monkeypatch.setattr(db, "live_catalogue", lambda: [
        liked, disliked, product_lookalike, image_lookalike, other
    ])
    monkeypatch.setattr(phase2_api, "build_pool", capture_pool)

    with pytest.raises(HTTPException) as exc:
        phase2_api.outfits(
            OutfitRequest(board_id="board-a", locked_ids=["disliked"])
        )

    assert exc.value.status_code == 409
    assert seen["ids"] == {"liked", "other"}
    assert "disliked" in seen["exclude_ids"]


def test_taste_update_board_id_consumes_only_target_signals(
    monkeypatch, tmp_path
):
    _create_board_pair(monkeypatch, tmp_path)
    _insert_tagged_items(
        _item("signal-a", "outerwear"),
        _item("signal-b", "top"),
    )
    db.insert_swipe("board-a", "signal-a", True)
    db.insert_swipe("board-b", "signal-b", False)
    output = TasteProfile.model_validate(_taste_profile())
    monkeypatch.setattr(
        taste,
        "call_task",
        lambda *_args: (
            output,
            SimpleNamespace(request_id="taste-update", latency_ms=5),
        ),
    )

    result = phase2_api.taste_update(TasteUpdateRequest(board_id="board-b"))

    assert result["version"] == 2
    assert not db.unconsumed_signals("board-b")["swipes"]
    assert [row["item_id"] for row in db.unconsumed_signals("board-a")["swipes"]] == [
        "signal-a"
    ]


def test_taste_history_board_id_reads_target_profile(monkeypatch, tmp_path):
    _create_board_pair(monkeypatch, tmp_path)
    items = [_item(f"signal-{index}", "outerwear") for index in range(3)]
    _insert_tagged_items(*items)
    for item in items:
        db.insert_swipe("board-b", item["id"], liked=True)

    result = phase2_api.taste_history(board_id="board-b")

    assert result["profile_id"] == "board-b"
    assert result["current"]["version"] == 1
    assert result["counts"]["swipes_since_update"] == 3


def test_taste_update_without_body_uses_active_profile(monkeypatch, tmp_path):
    _create_board_pair(monkeypatch, tmp_path)
    _insert_tagged_items(
        _item("signal-a", "outerwear"),
        _item("signal-b", "top"),
    )
    db.insert_swipe("board-a", "signal-a", True)
    db.insert_swipe("board-b", "signal-b", False)
    output = TasteProfile.model_validate(_taste_profile())
    monkeypatch.setattr(
        taste,
        "call_task",
        lambda *_args: (
            output,
            SimpleNamespace(request_id="taste-update", latency_ms=5),
        ),
    )

    result = phase2_api.taste_update()

    assert result["version"] == 2
    assert not db.unconsumed_signals("board-a")["swipes"]
    assert [row["item_id"] for row in db.unconsumed_signals("board-b")["swipes"]] == [
        "signal-b"
    ]


def test_swipe_deck_and_taste_reject_foreign_board_ids(monkeypatch):
    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-1")
    monkeypatch.setattr(
        db,
        "get_profile",
        lambda profile_id, kind=None: {
            "id": profile_id,
            "owner_id": "owner-2",
            "brief": _brief(),
        }
        if profile_id == "foreign" and kind == "user"
        else None,
    )
    monkeypatch.setattr(
        db,
        "active_profile",
        lambda *_args, **_kwargs: pytest.fail(
            "a supplied board_id must not use the active profile"
        ),
    )
    monkeypatch.setattr(
        db,
        "get_item_with_tags",
        lambda _item_id: pytest.fail("ownership must be checked before item lookup"),
    )

    requests = (
        lambda: phase2_api.deck(board_id="foreign"),
        lambda: phase2_api.swipe(
            SwipeRequest(item_id="item", liked=True, board_id="foreign")
        ),
        lambda: phase2_api.taste_update(TasteUpdateRequest(board_id="foreign")),
        lambda: phase2_api.taste_history(board_id="foreign"),
    )
    for request in requests:
        with pytest.raises(HTTPException) as exc:
            request()
        assert exc.value.status_code == 404
        assert exc.value.detail == "Board not found"


def test_swap_and_verdict_use_board_id_to_match_outfit(monkeypatch):
    board = {"id": "board-b", "owner_id": "owner-1", "brief": _brief()}
    outfit = {
        "id": "outfit-b",
        "profile_id": "board-b",
        "piece_ids": ["old"],
        "total_gbp": 30,
    }
    old = _item("old", "top")
    replacement = _item("replacement", "top")
    profile_ids = []

    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "owner-1")
    monkeypatch.setattr(
        db,
        "get_profile",
        lambda profile_id, kind=None: board
        if profile_id == "board-b" and kind == "user"
        else None,
    )
    monkeypatch.setattr(
        db,
        "active_profile",
        lambda _kind: {"id": "board-a", "brief": _brief()},
    )
    monkeypatch.setattr(db, "get_outfit", lambda _outfit_id: outfit)
    monkeypatch.setattr(db, "live_catalogue", lambda: [old, replacement])
    monkeypatch.setattr(db, "profile_swipe_items", lambda _profile_id: [])
    monkeypatch.setattr(
        db,
        "get_item_with_tags",
        lambda item_id: {"old": old, "replacement": replacement}.get(item_id),
    )
    monkeypatch.setattr(
        db,
        "update_outfit_pieces",
        lambda _outfit_id, piece_ids, total: outfit.update(
            piece_ids=piece_ids, total_gbp=total
        ),
    )
    monkeypatch.setattr(
        phase2_api,
        "_taste",
        lambda profile_id: (
            profile_ids.append(profile_id) or _taste_profile()
        ),
    )
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "rank_items", lambda *_args, **_kwargs: [replacement])
    monkeypatch.setattr(phase2_api, "layer_role", lambda _item: "base")
    monkeypatch.setattr(phase2_api, "base_layer_subtype", lambda _item: "other")
    monkeypatch.setattr(phase2_api, "is_outfit_eligible", lambda _item: True)
    monkeypatch.setattr(phase2_api, "outfit_errors", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(phase2_api, "order_outfit_pieces", lambda pieces: pieces)
    monkeypatch.setattr(phase2_api, "_public_outfit", lambda value: value)

    swapped = phase2_api.swap(
        "outfit-b", SwapRequest(item_id="old", board_id="board-b")
    )

    assert swapped["piece_ids"] == ["replacement"]
    assert profile_ids == ["board-b"]

    verdict_outfit = {
        "id": "outfit-b",
        "profile_id": "board-b",
        "piece_ids": ["replacement"],
    }
    monkeypatch.setattr(db, "get_outfit", lambda _outfit_id: verdict_outfit)
    monkeypatch.setattr(
        db,
        "update_outfit_verdict",
        lambda _outfit_id, verdict, reason: verdict_outfit.update(
            verdict=verdict, reason=reason
        ),
    )
    monkeypatch.setattr(
        phase2_api,
        "update_taste",
        lambda profile: (
            profile_ids.append(profile["id"])
            or (2, _taste_profile(), SimpleNamespace(request_id="taste-2"), None)
        ),
    )

    result = phase2_api.verdict(
        "outfit-b", VerdictRequest(verdict="like", board_id="board-b")
    )

    assert result["outfit"]["verdict"] == "like"
    assert profile_ids == ["board-b", "board-b"]

    for handler in (
        lambda: phase2_api.swap(
            "outfit-b", SwapRequest(item_id="replacement", board_id="board-b")
        ),
        lambda: phase2_api.verdict(
            "outfit-b", VerdictRequest(verdict="like", board_id="board-b")
        ),
    ):
        mismatch = dict(verdict_outfit, profile_id="board-a")
        monkeypatch.setattr(db, "get_outfit", lambda _outfit_id: mismatch)
        with pytest.raises(HTTPException) as exc:
            handler()
        assert exc.value.status_code == 404
        assert exc.value.detail == "Outfit not found"


def test_warmup_persona_uses_stubbed_calls_and_warmup_profile_kind(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "warmup.sqlite")
    db.init_db()
    items = []
    tagged_items = []
    for group in range(3):
        for slot in ("top", "bottom", "shoes"):
            item = _item(f"item-{group}-{slot}", slot)
            items.append({key: value for key, value in item.items() if key != "tags"})
            tagged_items.append(item)
    db.insert_items(items)
    for item in tagged_items:
        db.save_tags(item["id"], item["tags"], "mreq-tag")
    taste_result = TasteProfile.model_validate(_taste_profile())
    taste_calls = []
    stylist_calls = []

    def fake_taste_call(task, _system, _model, _content, _out_model):
        taste_calls.append(task)
        return taste_result, SimpleNamespace(request_id=f"taste-{len(taste_calls)}")

    def fake_stylist_call(task, _system, _model, _content, _out_model):
        stylist_calls.append(task)
        outfits = []
        for group in range(3):
            outfits.append(
                Outfit(
                    title=f"Look {group}",
                    mood="Casual",
                    piece_ids=[
                        f"item-{group}-top",
                        f"item-{group}-bottom",
                        f"item-{group}-shoes",
                    ],
                    why="A balanced look.",
                )
            )
        return StylistOutput(outfits=outfits), SimpleNamespace(
            request_id=f"stylist-{len(stylist_calls)}"
        )

    monkeypatch.setattr(taste, "call_task", fake_taste_call)
    monkeypatch.setattr(warmup, "call_task", fake_stylist_call)
    result = warmup.run_persona(
        {
            "name": "Test persona",
            "brief": _brief(),
            "keep_words": ["shirt"],
            "reject_reason": "Too formal",
        }
    )

    profile = db.get_profile(result["profile_id"], kind="warmup")
    assert profile is not None
    assert profile["active"] == 0
    assert db.active_profile("user") is None
    assert len(result["rounds"]) == 3
    assert result["rounds"][-1]["version"] == 10
    assert len(taste_calls) == 10
    assert len(stylist_calls) == 3


def test_fetch_job_state_persists_and_looks_up_latest_job_for_profile(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fetch.sqlite")
    db.init_db()
    first = {
        "id": "job-1",
        "started_at": "2025-01-01T00:00:00+00:00",
        "status": "done",
        "fetched": 2,
        "tagged": 2,
        "failed": 0,
        "errors": [],
    }
    second = {
        "id": "job-2",
        "started_at": "2025-01-02T00:00:00+00:00",
        "status": "running",
        "fetched": 0,
        "tagged": 0,
        "failed": 0,
        "errors": [],
    }
    db.save_fetch_job("job-1", "board-1", first)
    db.save_fetch_job("job-2", "board-1", second)
    db.update_fetch_job("job-1", first)

    assert not hasattr(phase2_api, "FETCH_JOBS")
    assert phase2_api.fetch_job("job-1")["fetched"] == 2
    assert phase2_api.fetch_job("board-1")["status"] == "running"
    assert phase2_api.fetch_job("board-1")["fetched"] == 0
