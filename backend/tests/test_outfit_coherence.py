from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import outfits, phase2_api
from app.outfits import (
    build_pool,
    is_outfit_eligible,
    order_outfit_pieces,
    validate_outfits,
    warmth,
)
from app.schemas import OutfitRequest, SwapRequest


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


def _validate(
    pieces: list[dict], *, include_shoes: bool = True
) -> tuple[list[dict], list[str]]:
    outfit = {
        "title": "Coherent look",
        "mood": "Relaxed workwear",
        "piece_ids": [piece["id"] for piece in pieces],
        "why": "A balanced outfit.",
    }
    return validate_outfits(
        [outfit], pieces, 1, [], 1000, include_shoes=include_shoes
    )


@pytest.mark.parametrize(
    ("title", "category", "expected"),
    [
        ("Sports Bra", "", False),
        ("High Leg Bikini", "bikini", False),
        ("Nieuwland Wetsuit", "wetsuit", False),
        ("Brand Logo Tee", "", True),
        ("braided belt", "belt", True),
    ],
)
def test_outfit_eligibility_excludes_non_outfit_base_items(
    title, category, expected
):
    assert is_outfit_eligible(
        _piece("piece", "top", category, title=title)
    ) is expected


@pytest.mark.parametrize(
    ("slot", "category", "expected"),
    [
        ("bottom", "cargo shorts", "warm"),
        ("top", "tank top", "warm"),
        ("top", "vest top", "warm"),
        ("top", "cami top", "warm"),
        ("top", "camisole", "warm"),
        ("top", "strappy top", "warm"),
        ("shoes", "leather sandals", "warm"),
        ("shoes", "slides", "warm"),
        ("shoes", "flip flops", "warm"),
        ("shoes", "espadrilles", "warm"),
        ("outerwear", "down jacket", "cold"),
        ("outerwear", "down vest", "cold"),
        ("outerwear", "padded coat", "cold"),
        ("outerwear", "quilted parka", "cold"),
        ("outerwear", "shearling coat", "cold"),
        ("outerwear", "peacoat", "cold"),
        ("outerwear", "duffle coat", "cold"),
        ("outerwear", "trench coat", "cold"),
        ("outerwear", "sheepskin coat", "cold"),
        ("accessory", "wool scarf", "cold"),
        ("accessory", "leather gloves", "cold"),
        ("accessory", "mittens", "cold"),
        ("accessory", "beanie", "cold"),
        ("accessory", "balaclava", "cold"),
        ("accessory", "earmuffs", "cold"),
        ("top", "button-down shirt", None),
        ("outerwear", "button-down jacket", None),
        ("top", "gilet vest", None),
        ("outerwear", "button-down shirt", None),
        ("outerwear", "windbreaker", None),
    ],
)
def test_warmth_classification(slot, category, expected):
    assert warmth(_piece("piece", slot, category)) == expected


@pytest.mark.parametrize(
    "pieces",
    [
        [
            _piece("tee", "top", "t-shirt"),
            _piece("shorts", "bottom", "cargo shorts"),
            _piece("parka", "outerwear", "parka"),
            _piece("shoes", "shoes", "boots"),
        ],
        [
            _piece("tee", "top", "t-shirt"),
            _piece("jeans", "bottom", "jeans"),
            _piece("puffer", "outerwear", "puffer jacket"),
            _piece("sandals", "shoes", "sandals"),
        ],
        [
            _piece("tank", "top", "tank top"),
            _piece("jeans", "bottom", "jeans"),
            _piece("shoes", "shoes", "boots"),
            _piece("scarf", "accessory", "wool scarf"),
        ],
    ],
)
def test_validation_rejects_warm_and_cold_pieces_together(pieces):
    valid, errors = _validate(pieces)

    assert not valid
    assert any(
        "warm-weather piece with cold-weather piece "
        "(e.g. shorts with a heavy coat)" in error
        for error in errors
    )


def test_validation_allows_shorts_with_light_outer_and_mid_layers():
    pieces = [
        _piece("tee", "top", "t-shirt"),
        _piece("hoodie", "top", "hoodie"),
        _piece("windbreaker", "outerwear", "windbreaker"),
        _piece("shorts", "bottom", "cargo shorts"),
        _piece("shoes", "shoes", "trainers"),
    ]

    valid, errors = _validate(pieces)

    assert len(valid) == 1
    assert errors == []


def test_validation_limits_bag_and_accessories_combined():
    base = [
        _piece("tee", "top", "t-shirt"),
        _piece("jeans", "bottom", "jeans"),
        _piece("shoes", "shoes", "boots"),
        _piece("bag", "bag", "tote bag"),
    ]
    accessories = [
        _piece("scarf", "accessory", "scarf"),
        _piece("belt", "accessory", "belt"),
    ]

    valid, errors = _validate([*base, *accessories])
    assert not valid
    assert any("more than 2 bag/accessory pieces" in error for error in errors)

    valid, errors = _validate([*base, accessories[0]])
    assert len(valid) == 1
    assert errors == []


def test_stylist_pool_line_includes_warmth_tag():
    item = _piece("shorts", "bottom", "cargo shorts")

    content = outfits.stylist_user_content({}, 1, [], [item])

    assert "shorts | bottom | - | warm | £30 | cargo shorts" in content


def test_build_pool_reserves_tees_and_filters_ineligible_items(monkeypatch):
    non_tees = [
        _piece(f"shirt-{index}", "top", "button-up shirt")
        for index in range(6)
    ]
    tees = [_piece(f"tee-{index}", "top", "graphic t-shirt") for index in range(6)]
    sports_bra = _piece("sports-bra", "top", "sports bra")
    ranked = [*non_tees, *tees, sports_bra]
    monkeypatch.setattr(outfits, "rank_items", lambda *_args, **_kwargs: ranked)

    pool = build_pool(ranked, {}, {}, ["sports-bra"], [])
    pool_ids = {item["id"] for item in pool}
    tee_ids = {item["id"] for item in tees}

    assert len(pool_ids & tee_ids) == 5
    assert "sports-bra" not in pool_ids


def _run_swap(
    monkeypatch,
    existing: list[dict],
    candidates: list[dict],
    replaced_id: str,
    *,
    exclude_ids: list[str] | None = None,
    include_shoes: bool = True,
):
    outfit = {
        "id": "outfit",
        "profile_id": "profile",
        "title": "Swap test",
        "mood": "Relaxed",
        "why": "A coherent outfit.",
        "piece_ids": [item["id"] for item in existing],
        "total_gbp": sum(item["price_gbp"] for item in existing),
    }
    items_by_id = {item["id"]: item for item in [*existing, *candidates]}
    updates = []
    monkeypatch.setattr(
        phase2_api,
        "_user_profile",
        lambda: {
            "id": "profile",
            "brief": {"include_shoes": include_shoes},
        },
    )
    monkeypatch.setattr(
        phase2_api, "_taste", lambda _profile_id: {"price_ceiling_gbp": 1000}
    )
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api.db, "get_outfit", lambda _outfit_id: outfit)
    monkeypatch.setattr(
        phase2_api.db, "live_catalogue", lambda: candidates
    )
    monkeypatch.setattr(
        phase2_api,
        "rank_items",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        phase2_api.db,
        "get_item_with_tags",
        lambda item_id: items_by_id.get(item_id),
    )

    def update_outfit(_outfit_id, piece_ids, total_gbp):
        outfit["piece_ids"] = list(piece_ids)
        outfit["total_gbp"] = total_gbp
        updates.append((list(piece_ids), total_gbp))

    monkeypatch.setattr(phase2_api.db, "update_outfit_pieces", update_outfit)
    result = phase2_api.swap(
        "outfit",
        SwapRequest(item_id=replaced_id, exclude_ids=exclude_ids or []),
    )
    return result, outfit, updates


def test_swap_accepts_exclusions_and_interleaves_base_subtypes(monkeypatch):
    old_tee = _piece("old-tee", "top", "t-shirt")
    jeans = _piece("jeans", "bottom", "jeans")
    shoes = _piece("shoes", "shoes", "boots")
    sports_bra = _piece("sports-bra", "top", "sports bra")
    best_tee = _piece("best-tee", "top", "graphic tee")
    shirt = _piece("shirt", "top", "button-up shirt")
    polo = _piece("polo", "top", "polo shirt")
    blouse = _piece("blouse", "top", "blouse")
    next_tee = _piece("next-tee", "top", "plain t-shirt")
    next_shirt = _piece("next-shirt", "top", "oxford shirt")

    request = SwapRequest.model_validate(
        {"item_id": "old-tee", "exclude_ids": ["best-tee"]}
    )
    assert request.exclude_ids == ["best-tee"]

    result, stored, _updates = _run_swap(
        monkeypatch,
        [shoes, old_tee, jeans],
        [
            sports_bra,
            best_tee,
            shirt,
            polo,
            blouse,
            next_tee,
            next_shirt,
        ],
        "old-tee",
        exclude_ids=request.exclude_ids,
    )

    assert [item["id"] for item in result["pieces"]] == [
        "shirt",
        "jeans",
        "shoes",
    ]
    assert stored["piece_ids"] == ["shirt", "jeans", "shoes"]


def test_swap_skips_candidates_that_create_a_weather_clash(monkeypatch):
    tee = _piece("tee", "top", "t-shirt")
    coat = _piece("coat", "outerwear", "wool coat")
    jeans = _piece("jeans", "bottom", "jeans")
    shoes = _piece("shoes", "shoes", "boots")
    shorts = _piece("shorts", "bottom", "cargo shorts")
    trousers = _piece("trousers", "bottom", "chinos")

    result, stored, _updates = _run_swap(
        monkeypatch,
        [shoes, jeans, coat, tee],
        [shorts, trousers],
        "jeans",
    )

    assert [item["id"] for item in result["pieces"]] == [
        "coat",
        "tee",
        "trousers",
        "shoes",
    ]
    assert "shorts" not in stored["piece_ids"]


def test_swap_keeps_409_when_no_candidate_preserves_the_outfit(monkeypatch):
    tee = _piece("tee", "top", "t-shirt")
    coat = _piece("coat", "outerwear", "wool coat")
    jeans = _piece("jeans", "bottom", "jeans")
    shoes = _piece("shoes", "shoes", "boots")
    shorts = _piece("shorts", "bottom", "cargo shorts")

    with pytest.raises(HTTPException) as exc_info:
        _run_swap(
            monkeypatch,
            [tee, coat, jeans, shoes],
            [shorts],
            "jeans",
        )

    assert exc_info.value.status_code == 409


def test_order_outfit_pieces_uses_canonical_groups_and_is_stable():
    pieces = [
        _piece("accessory", "accessory", "belt"),
        _piece("shoes", "shoes", "boots"),
        _piece("bottom", "bottom", "jeans"),
        _piece("dress", "dress", "midi dress"),
        _piece("tee-1", "top", "t-shirt"),
        _piece("outer", "outerwear", "coat"),
        _piece("tee-2", "top", "t-shirt"),
        _piece("mid", "knitwear", "jumper"),
        _piece("bag", "bag", "tote bag"),
    ]

    ordered = order_outfit_pieces(pieces)

    assert [item["id"] for item in ordered] == [
        "outer",
        "mid",
        "tee-1",
        "tee-2",
        "dress",
        "bottom",
        "shoes",
        "bag",
        "accessory",
    ]


def test_public_outfit_orders_legacy_stored_pieces(monkeypatch):
    pieces = [
        _piece("bag", "bag", "tote bag"),
        _piece("shoes", "shoes", "boots"),
        _piece("bottom", "bottom", "jeans"),
        _piece("tee", "top", "t-shirt"),
        _piece("coat", "outerwear", "coat"),
    ]
    items_by_id = {item["id"]: item for item in pieces}
    monkeypatch.setattr(
        phase2_api.db,
        "get_item_with_tags",
        lambda item_id: items_by_id.get(item_id),
    )

    outfit = phase2_api._public_outfit(
        {
            "id": "outfit",
            "title": "Look",
            "mood": "Calm",
            "why": "Coherent",
            "piece_ids": ["bag", "shoes", "bottom", "tee", "coat"],
            "total_gbp": 150,
        }
    )

    assert [item["id"] for item in outfit["pieces"]] == [
        "coat",
        "tee",
        "bottom",
        "shoes",
        "bag",
    ]


def test_outfits_saves_and_returns_piece_ids_in_canonical_order(monkeypatch):
    pieces = [
        _piece("accessory", "accessory", "belt"),
        _piece("shoes", "shoes", "boots"),
        _piece("bottom", "bottom", "jeans"),
        _piece("tee", "top", "t-shirt"),
        _piece("bag", "bag", "tote bag"),
        _piece("mid", "top", "hoodie"),
        _piece("outer", "outerwear", "coat"),
    ]
    stylist_outfit = {
        "title": "Layered look",
        "mood": "Relaxed",
        "piece_ids": [
            "tee",
            "bottom",
            "shoes",
            "accessory",
            "bag",
            "mid",
            "outer",
        ],
        "why": "A coherent layered outfit.",
    }
    model_outfit = SimpleNamespace(model_dump=lambda: stylist_outfit)
    model_result = SimpleNamespace(outfits=[model_outfit])
    metadata = SimpleNamespace(request_id="request")
    saved = {}
    monkeypatch.setattr(
        phase2_api,
        "_user_profile",
        lambda: {
            "id": "profile",
            "brief": {"include_shoes": True},
        },
    )
    monkeypatch.setattr(
        phase2_api.db,
        "latest_taste",
        lambda _profile_id: {
            "version": 1,
            "profile": {"price_ceiling_gbp": 1000},
        },
    )
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: pieces)
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(
        phase2_api, "build_pool", lambda *_args, **_kwargs: pieces
    )
    monkeypatch.setattr(
        phase2_api, "stylist_user_content", lambda *_args, **_kwargs: "pool"
    )
    monkeypatch.setattr(phase2_api, "style_notes", lambda _brief: [])
    monkeypatch.setattr(phase2_api.db, "swipe_history", lambda _profile_id: {})
    monkeypatch.setattr(
        phase2_api,
        "call_task",
        lambda *_args, **_kwargs: (model_result, metadata),
    )

    def save_outfit(_profile_id, _version, outfit, piece_ids, _total, _request_id):
        saved["piece_ids"] = list(piece_ids)
        saved["outfit_piece_ids"] = list(outfit["piece_ids"])
        return "saved-outfit"

    monkeypatch.setattr(phase2_api.db, "save_outfit", save_outfit)

    result = phase2_api.outfits(OutfitRequest(count=1))

    expected = ["outer", "mid", "tee", "bottom", "shoes", "bag", "accessory"]
    assert saved["piece_ids"] == expected
    assert saved["outfit_piece_ids"] == expected
    assert [item["id"] for item in result["outfits"][0]["pieces"]] == expected
