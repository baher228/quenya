import pytest

from app import db, rank, resettle, taste


def _profile(*, likes=None, avoids=None, changes=None):
    return {
        "summary": "style",
        "likes": likes or {},
        "avoids": avoids or {},
        "rules": [],
        "palette": [],
        "niche_level": 0.5,
        "price_ceiling_gbp": 200,
        "condition_preference": "both",
        "search_queries": [],
        "changes": changes or [],
    }


def _brief():
    return {
        "text": "Relaxed navy casual",
        "looking_for": "outfits",
        "pieces": [],
        "gender": "both",
        "condition": "both",
        "niche": 0.5,
        "budget_gbp": 200,
        "sizes": {},
    }


def test_norm_token_and_clean_keys_canonicalize_merge_and_drop_junk():
    assert rank.norm_token("colours:black") == "black"
    assert rank.norm_token("color_navy") == "navy"
    assert rank.norm_token("style_workwear") == "workwear"
    assert rank.norm_token("pattern_cable_knit") == "cable knit"
    cleaned = taste.clean_keys(
        _profile(
            likes={
                "categories_outerwear": 0.9,
                "aesthetics": 0.9,
                "menswear": 0.9,
                "condition_second-hand": 0.9,
                "multi-colour": 0.4,
                "multi_colour": 0.8,
            }
        )
    )
    assert cleaned["likes"] == {"multi colour": 0.8}


def test_settlement_uses_keep_rate_adjusted_evidence_and_canonical_keys():
    previous = _profile()
    picky, blocked = taste.settle_profile(
        _profile(avoids={"brown": 0.9}),
        previous,
        {"brown": {"kept": 0, "passed": 6}},
        "",
        base_rate=0.125,
    )
    assert picky["avoids"] == {}
    assert blocked == {"brown"}

    supported, blocked = taste.settle_profile(
        _profile(avoids={"relaxed": 0.9}),
        previous,
        {"relaxed": {"kept": 5, "passed": 15}},
        "",
        base_rate=0.45,
    )
    assert supported["avoids"] == {"relaxed": 0.5}
    assert blocked == set()


def test_contradicted_existing_avoid_decays_or_is_removed():
    previous = _profile(avoids={"workwear": 0.6})
    new = _profile(avoids={"workwear": 0.6})
    evidence = {"workwear": {"kept": 5, "passed": 2}}
    updated, _ = taste.settle_profile(new, previous, evidence, "", base_rate=0.43)
    assert updated["avoids"] == {"workwear": pytest.approx(0.3)}
    cleaned, blocked = taste.settle_profile(
        previous,
        previous,
        evidence,
        "",
        base_rate=0.43,
        mode="cleanup",
    )
    assert cleaned["avoids"] == {}
    assert blocked == {"workwear"}


def test_unseen_new_keys_are_blocked_but_brief_keys_are_capped():
    previous = _profile()
    unseen, blocked = taste.settle_profile(
        _profile(avoids={"neon": 0.9}), previous, {}, ""
    )
    assert unseen["avoids"] == {}
    assert blocked == {"neon"}
    protected, blocked = taste.settle_profile(
        _profile(avoids={"pirate": 0.9}),
        previous,
        {},
        "",
        brief_text="pirate style",
    )
    assert protected["avoids"] == {"pirate": 0.5}
    assert blocked == set()


def test_history_protects_existing_avoid_and_clean_keys_resolves_contradictions():
    previous = _profile(avoids={"logos": 0.8})
    protected, _ = taste.settle_profile(
        previous,
        previous,
        {},
        "",
        history_text="outfit rejected: no big logos",
    )
    assert protected["avoids"] == {"logos": 0.8}
    cleaned = taste.clean_keys(
        _profile(likes={"baggy": 0.8}, avoids={"fit_baggy": 0.6}),
        {"baggy": {"kept": 6, "passed": 2}},
        0.4,
    )
    assert cleaned["likes"] == {"baggy": 0.8}
    assert cleaned["avoids"] == {}


def test_rank_uses_exact_and_whole_word_matches():
    item = {"tags": {"aesthetics": [], "colours": ["black"], "niche": 0}}
    base = {"likes": {}, "avoids": {}, "niche_level": 0}
    assert rank.score_item(item, {**base, "likes": {"colours:black": 0.8}}) == pytest.approx(
        rank.score_item(item, {**base, "likes": {"black": 0.8}})
    )
    tailored = {
        "tags": {
            "aesthetics": [],
            "colours": [],
            "category": "tailored trousers",
            "niche": 0,
        }
    }
    assert rank.score_item(tailored, {**base, "likes": {"red": 1}}) == 0


def test_resettle_dry_run_and_apply_preserve_older_versions(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "taste.sqlite")
    db.init_db()
    db.create_profile("board", _brief(), label="Test board")
    db.insert_items(
        [
            {
                "id": "item",
                "source": "test",
                "source_url": "https://example.test/item",
                "title": "workwear jacket",
                "price_gbp": 20,
                "image_url": "https://example.test/item.jpg",
                "raw_json": {},
            }
        ]
    )
    db.save_tags(
        "item",
        {
            "slot": "outerwear",
            "category": "jacket",
            "aesthetics": ["workwear"],
            "colours": [],
            "materials": [],
            "fit": "regular",
            "era": "contemporary",
        },
        "tag-request",
    )
    db.insert_items(
        [
            {
                "id": "other",
                "source": "test",
                "source_url": "https://example.test/other",
                "title": "plain shirt",
                "price_gbp": 20,
                "image_url": "https://example.test/other.jpg",
                "raw_json": {},
            }
        ]
    )
    db.save_tags(
        "other",
        {
            "slot": "top",
            "category": "shirt",
            "aesthetics": [],
            "colours": [],
            "materials": [],
            "fit": "regular",
            "era": "contemporary",
        },
        "tag-request",
    )
    for liked in [True] * 5 + [False] * 2:
        db.insert_swipe("board", "item", liked)
    for _ in range(2):
        db.insert_swipe("board", "other", False)
    original = _profile(
        likes={"color_navy": 0.6},
        avoids={"workwear": 0.6, "aesthetics": 0.9, "neon": 0.7},
    )
    db.save_taste_version("board", 1, original, {}, "taste-request")

    assert resettle.resettle("board", apply=False) == 1
    assert "reason=protected" in capsys.readouterr().out
    assert db.latest_taste("board")["version"] == 1
    assert resettle.resettle("board", apply=True) == 1
    latest = db.latest_taste("board")
    assert latest["version"] == 2
    assert latest["profile"]["likes"] == {"navy": 0.6}
    assert latest["profile"]["avoids"] == {"neon": 0.7}
    assert db.get_taste_history("board")[-1]["version"] == 1
    assert "Tidied up: dropped 2" in latest["profile"]["changes"][0]
