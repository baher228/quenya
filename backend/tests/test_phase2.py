import json
from types import SimpleNamespace

import pytest

from app import db, phase2_api, taste, warmup
from app.outfits import validate_outfits
from app.rank import rank_items
from app.schemas import CheckoutRequest, Outfit, OutfitRequest, StylistOutput, TasteProfile


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
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: items)
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
                    piece_ids=["outside-pool"] if invalid else ["top", "bottom", "shoes"],
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
        "shoes",
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
    monkeypatch.setattr(phase2_api.db, "get_outfit", lambda _outfit_id: outfit)
    monkeypatch.setattr(phase2_api.db, "get_item", lambda item_id: items[item_id])

    result = phase2_api.checkout(CheckoutRequest(outfit_id="outfit-1"))

    assert result["simulated"] is True
    assert result["total_gbp"] == 42.5
    assert result["outfit_id"] == "outfit-1"
    assert len(result["lines"]) == 2
    assert result["order_ref"].startswith("DEMO-")


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
