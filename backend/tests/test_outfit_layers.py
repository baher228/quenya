import pytest

from app import outfits, phase2_api
from app.outfits import (
    build_pool,
    layer_role,
    stylist_user_content,
    validate_outfits,
)
from app.prompts import PROMPT_VERSION, STYLIST_SYSTEM
from app.schemas import SwapRequest


def _piece(
    item_id: str,
    slot: str,
    category: str,
    *,
    title: str | None = None,
    price: float = 30,
) -> dict:
    return {
        "id": item_id,
        "title": title or category,
        "price_gbp": price,
        "source": "shop.example",
        "tags": {
            "slot": slot,
            "category": category,
            "colours": ["black"],
            "aesthetics": ["workwear"],
            "niche": 0.2,
            "fit": "regular",
        },
    }


@pytest.mark.parametrize(
    ("category", "slot", "expected"),
    [
        ("graphic t-shirt", "top", "base"),
        ("graphic hoodie", "top", "mid"),
        ("tech fleece zip-up hoodie", "outerwear", "mid"),
        ("puffer jacket", "outerwear", "outer"),
        ("fleece jacket", "outerwear", "outer"),
        ("merino wool polo shirt", "knitwear", "base"),
        ("quarter zip sweatshirt", "top", "mid"),
        ("crew neck jumper", "knitwear", "mid"),
        ("overshirt", "outerwear", "mid"),
        ("pointelle jersey vest", "top", "base"),
        ("sports bra", "top", "base"),
        ("trench coat", "outerwear", "outer"),
        ("two-piece suit", "outerwear", "outer"),
    ],
)
def test_layer_role_categories(category, slot, expected):
    assert layer_role(_piece("piece", slot, category)) == expected


def test_layer_role_uses_title_only_when_category_is_empty():
    assert layer_role(
        _piece("piece", "top", "", title="Graphic Hoodie")
    ) == "mid"


@pytest.mark.parametrize(
    ("slot", "expected"),
    [("top", "base"), ("knitwear", "mid"), ("outerwear", "outer")],
)
def test_layer_role_uses_slot_default(slot, expected):
    assert layer_role(_piece("piece", slot, "unclassified")) == expected


def test_layer_role_ignores_non_layer_slots():
    assert layer_role(_piece("piece", "shoes", "puffer jacket")) is None


@pytest.fixture
def outfit_pool():
    return [
        _piece("tee", "top", "graphic t-shirt"),
        _piece("tee-2", "top", "plain tee"),
        _piece("hoodie", "top", "graphic hoodie"),
        _piece("zip-hoodie", "outerwear", "zip hoodie"),
        _piece("jumper", "knitwear", "crew neck jumper"),
        _piece("coat", "outerwear", "wool coat"),
        _piece("puffer", "outerwear", "puffer jacket"),
        _piece("trench", "outerwear", "trench coat"),
        _piece("jeans", "bottom", "jeans"),
        _piece("shorts", "bottom", "shorts"),
        _piece("dress", "dress", "midi dress"),
        _piece("shoes", "shoes", "boots"),
        _piece("bag", "bag", "tote bag"),
        _piece("bag-2", "bag", "crossbody bag"),
        _piece("accessory-1", "accessory", "belt"),
        _piece("accessory-2", "accessory", "scarf"),
        _piece("accessory-3", "accessory", "cap"),
    ]


def _validate(piece_ids, pool, *, include_shoes=True):
    outfit = {
        "title": "Layered look",
        "mood": "Relaxed workwear",
        "piece_ids": piece_ids,
        "why": "A balanced outfit.",
    }
    return validate_outfits(
        [outfit], pool, 1, [], 1000, include_shoes=include_shoes
    )


@pytest.mark.parametrize(
    ("piece_ids", "include_shoes"),
    [
        (["tee", "hoodie", "coat", "jeans", "shoes"], True),
        (
            [
                "tee", "jumper", "coat", "jeans", "shoes", "bag",
                "accessory-1", "accessory-2",
            ],
            True,
        ),
        (["dress", "coat", "shoes"], True),
        (["tee", "jeans"], False),
    ],
)
def test_validate_outfits_accepts_layered_compositions(
    outfit_pool, piece_ids, include_shoes
):
    valid, errors = _validate(piece_ids, outfit_pool, include_shoes=include_shoes)
    assert len(valid) == 1
    assert errors == []


@pytest.mark.parametrize(
    ("piece_ids", "expected_error"),
    [
        (["hoodie", "jeans", "shoes"], "needs exactly one base layer"),
        (["tee", "tee-2", "jeans", "shoes"], "needs exactly one base layer"),
        (
            ["tee", "puffer", "trench", "jeans", "shoes"],
            "two outer layers",
        ),
        (
            ["tee", "hoodie", "zip-hoodie", "jeans", "shoes"],
            "two mid layers",
        ),
        (
            ["tee", "jeans", "shorts", "hoodie", "coat", "shoes"],
            "more than 4 clothing pieces",
        ),
        (
            ["tee", "jeans", "shorts", "shoes"],
            "needs exactly one bottom",
        ),
        (
            ["dress", "tee", "jeans", "shoes"],
            "a dress outfit cannot include a base layer",
        ),
    ],
)
def test_validate_outfits_rejects_invalid_layer_compositions(
    outfit_pool, piece_ids, expected_error
):
    valid, errors = _validate(piece_ids, outfit_pool)
    assert not valid
    assert any(expected_error in error for error in errors)


def test_validate_outfits_enforces_bag_and_accessory_limits(outfit_pool):
    valid, errors = _validate(
        ["tee", "jeans", "shoes", "bag", "bag-2", "accessory-1",
         "accessory-2", "accessory-3"],
        outfit_pool,
    )
    assert not valid
    assert any("more than one bag" in error for error in errors)
    assert any("more than two accessories" in error for error in errors)


def test_build_pool_keeps_base_items_beyond_top_ten_hoodies(monkeypatch):
    ranked = [
        _piece(f"hoodie-{index}", "top", "graphic hoodie")
        for index in range(10)
    ]
    ranked.append(_piece("low-ranked-tee", "top", "graphic t-shirt"))
    monkeypatch.setattr(outfits, "rank_items", lambda *_args, **_kwargs: ranked)

    pool = build_pool(ranked, {}, {}, [], [])

    assert "low-ranked-tee" in {item["id"] for item in pool}
    assert sum(layer_role(item) == "mid" for item in pool) == 8


def test_stylist_pool_lines_include_layer_roles_and_system_rules():
    tee = _piece("tee", "top", "graphic t-shirt")
    bag = _piece("bag", "bag", "tote bag")
    content = stylist_user_content({}, 1, [], [tee, bag])

    assert "tee | top | base | £30 | graphic t-shirt" in content
    assert "bag | bag | - | £30 | tote bag" in content
    assert PROMPT_VERSION == "2026-10-25.2"
    assert "one bottom plus exactly one base layer" in STYLIST_SYSTEM
    assert "Never use two pieces with the same layer role" in STYLIST_SYSTEM


def test_swap_keeps_the_replaced_layer_role(monkeypatch):
    replaced = _piece("old-tee", "top", "graphic t-shirt", price=20)
    wrong_role = _piece("hoodie", "top", "graphic hoodie", price=15)
    same_role = _piece("new-tee", "top", "plain tee", price=25)
    jeans = _piece("jeans", "bottom", "jeans", price=35)
    shoes = _piece("shoes", "shoes", "boots", price=40)
    outfit = {
        "id": "outfit-1",
        "profile_id": "profile-1",
        "piece_ids": ["old-tee", "jeans", "shoes"],
        "total_gbp": 95,
    }
    updated = {
        **outfit,
        "piece_ids": ["new-tee", "jeans", "shoes"],
        "total_gbp": 100,
    }
    get_outfit_results = iter((outfit, updated))
    items_by_id = {
        item["id"]: item for item in (replaced, wrong_role, same_role, jeans, shoes)
    }
    updates = []
    monkeypatch.setattr(
        phase2_api, "_user_profile", lambda: {"id": "profile-1", "brief": {}}
    )
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: {"price_ceiling_gbp": 200})
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api.db, "get_outfit", lambda _outfit_id: next(get_outfit_results))
    monkeypatch.setattr(
        phase2_api.db,
        "get_item_with_tags",
        lambda item_id: items_by_id.get(item_id),
    )
    monkeypatch.setattr(
        phase2_api.db, "live_catalogue", lambda: [wrong_role, same_role]
    )
    monkeypatch.setattr(
        phase2_api, "rank_items", lambda *_args, **_kwargs: [wrong_role, same_role]
    )
    monkeypatch.setattr(
        phase2_api.db,
        "update_outfit_pieces",
        lambda outfit_id, piece_ids, total: updates.append(
            (outfit_id, piece_ids, total)
        ),
    )
    monkeypatch.setattr(phase2_api, "_public_outfit", lambda result: result)

    result = phase2_api.swap("outfit-1", SwapRequest(item_id="old-tee"))

    assert updates == [("outfit-1", ["new-tee", "jeans", "shoes"], 100)]
    assert result["piece_ids"] == ["new-tee", "jeans", "shoes"]
