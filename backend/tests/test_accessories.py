import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import phase2_api
from app.accessories import (
    accessory_cap,
    accessory_gender,
    accessory_kind,
    is_accessory_slot,
    is_excluded,
    wants_accessories,
)
from app.schemas import SwapRequest


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Leather Belt", ("accessory", "belt")),
        ("Canvas Tote Bag", ("bag", "tote bag")),
        ("Ribbed Wool Beanie", ("accessory", "beanie")),
        ("Cotton Bonnet", ("accessory", "bonnet")),
        ("Cashmere Bonnets", ("accessory", "bonnet")),
        ("Pearl Headband", ("accessory", "headband")),
        ("Gold Hair Clip", ("accessory", "hair clip")),
        ("Gold Hair Clips", ("accessory", "hair clip")),
        ("Silk Twill Scarf", ("accessory", "scarf")),
        ("Tortoiseshell Sunglasses", ("accessory", "sunglasses")),
        ("Gold Hoop Earrings", ("accessory", "earrings")),
        ("Automatic Field Watch", ("accessory", "watch")),
        ("Leather Card Holder", ("accessory", "card holder")),
        ("Leather Gloves", ("accessory", "gloves")),
        ("Ribbed Socks 3 Pack", ("accessory", "socks")),
        ("Nylon Belt Bag", ("bag", "bag")),
        ("Watch Cap", ("accessory", "cap")),
        ("Dress Watch", ("accessory", "watch")),
        ("The Weekender", ("bag", "bag")),
    ],
)
def test_accessory_kind(title, expected):
    product_type = "Bags" if title == "The Weekender" else ""
    assert accessory_kind(title, product_type) == expected


@pytest.mark.parametrize(
    ("title", "product_type"),
    [
        ("Shampoo & Conditioner Bundle", ""),
        ("The Menswear Book", ""),
        ("The Menswear Book", "Book"),
        ("Eau de Parfum 50ml", ""),
        ("Lip Balm", ""),
        ("Scented Candle", ""),
        ("Gift Card", ""),
        ("E-Gift Card", ""),
        ("iPhone Case", ""),
        ("Wash Bag", ""),
        ("Enamel Mug", ""),
        ("Notebook", ""),
        ("Kids Beanie", ""),
        ("Scarf Print Midi Dress", ""),
        ("Belted Wool Coat", ""),
        ("Cap Sleeve Top", ""),
        ("Sock Boots", ""),
        ("Bag-Print T-Shirt", ""),
        ("Leather Keyring", ""),
        ("Dog Collar", ""),
    ],
)
def test_excluded_and_non_accessory_titles(title, product_type):
    assert is_excluded(title, product_type) or accessory_kind(title, product_type) is None


@pytest.mark.parametrize(
    ("title", "product_type"),
    [
        ("The Miffy Face Bookmark", ""),
        ("The Luggage Tag - Red with Harvard H", ""),
        ("The Passport Cover - Strawberry Ice Cream", ""),
        ("Kevin Leather Key Lanyard in Bog", ""),
        ("Carbon Pro Spray", ""),
        ("Aries x LAIKA Wildwood Sticker Pack", ""),
        ("Organic Cotton-Canvas Apron", ""),
        ("Shrimps Pink Trinket Tray", ""),
        ("Hard Bristle Hat Brush", ""),
        ("Il Bisonte Leather Watch Case", ""),
        ("Il Bisonte Leather Playing Card Case", ""),
        ("Leather Belt", "Bookmark"),
        ("Leather Belt", "Watch Case"),
        ("Leather Belt", "Care Kit"),
        ("Glassware", ""),
        ("Luxury Gift Box", ""),
        ("Gift Box", ""),
        ("Gift Set", ""),
        ("Gift Wrap", ""),
        ("Gift Card Socks", ""),
        ("Playing Cards", ""),
        ("Il Bisonte Leather Playing Card Case", ""),
        ("Crystal Wine Glasses", ""),
    ],
)
def test_non_fashion_leaks_are_strictly_excluded(title, product_type):
    assert is_excluded(title, product_type)
    assert accessory_kind(title, product_type) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Brush Print Silk Bandana", ("accessory", "bandana")),
        ("Glass Bead Necklace", ("accessory", "necklace")),
        (
            "July Birthstone Pendant Necklace | 18ct Gold Vermeil/Glass Filled Ruby",
            ("accessory", "necklace"),
        ),
        ("Men's Playing Cards Cotton Socks", ("accessory", "socks")),
        ("Il Bisonte Leather Card Case", ("accessory", "card case")),
        ("Crossbody Strap", ("bag", "crossbody bag")),
        ("Mako Bottle Bag", ("bag", "bag")),
        ("Charm Pin Brooch", ("accessory", "brooch")),
        ("Socks Gift Box", ("accessory", "socks")),
        ("Men's 4-Pair Socks Gift Box", ("accessory", "socks")),
        ("Box Set Beanie and Scarf", ("accessory", "scarf")),
    ],
)
def test_accessory_titles_survive_gift_packaging_exclusions(title, expected):
    assert not is_excluded(title)
    assert accessory_kind(title) == expected


def test_product_type_fallback_tags_and_exclusions():
    assert accessory_kind("The Weekender", "Bags") == ("bag", "bag")
    assert accessory_kind("Classic", "", ["Belts"]) == ("accessory", "belt")
    assert accessory_kind("Leather Belt", "Book") is None
    assert is_excluded("Novel", "Books")
    assert not is_excluded("The Scarf Print Midi Dress")
    assert accessory_kind("Scarf Print Midi Dress") is None
    assert is_excluded("Sunglasses", "Print")


@pytest.mark.parametrize(
    ("title", "category", "expected"),
    [
        ("Women's Leather Belt", "belt", "womenswear"),
        ("Men's Cashmere Scarf", "scarf", "menswear"),
        ("Wool Beanie", "beanie", "unisex"),
        ("Leather Handbag", "handbag", "womenswear"),
        ("Leather Clutch", "clutch", "womenswear"),
        ("Silver Cufflinks", "cufflinks", "menswear"),
    ],
)
def test_accessory_gender(title, category, expected):
    assert accessory_gender(title, category=category) == expected


def test_accessory_cap_intent_and_slot_helpers():
    assert wants_accessories({"pieces": ["accessory"]})
    assert wants_accessories({"pieces": ["bag"]})
    assert wants_accessories({"text": "navy suit with a leather belt"})
    assert wants_accessories({"text": "build outfits with accessories"})
    assert not wants_accessories({"text": "navy suit"})
    assert accessory_cap(10, {}) == 1
    assert accessory_cap(10, {"pieces": ["accessory"]}) == 10
    assert accessory_cap(10, {"text": "navy suit with a leather belt"}) == 10
    assert is_accessory_slot("bag")
    assert is_accessory_slot("accessory")
    assert not is_accessory_slot("shoes")


def test_swap_endpoint_replaces_accessory_with_same_slot_under_price_ceiling(monkeypatch):
    outfit = {
        "id": "outfit",
        "profile_id": "profile",
        "piece_ids": ["top", "bottom", "shoes", "belt"],
        "total_gbp": 90,
    }
    replaced = {
        "id": "belt",
        "price_gbp": 30,
        "tags": {"slot": "accessory"},
    }
    too_expensive = {
        "id": "expensive",
        "price_gbp": 80,
        "tags": {"slot": "accessory"},
    }
    replacement = {
        "id": "replacement",
        "price_gbp": 35,
        "tags": {"slot": "accessory"},
    }
    monkeypatch.setattr(phase2_api, "_user_profile", lambda: {"id": "profile", "brief": {}})
    monkeypatch.setattr(phase2_api, "_taste", lambda _profile_id: {"price_ceiling_gbp": 100})
    monkeypatch.setattr(phase2_api, "_core", lambda: {})
    monkeypatch.setattr(phase2_api.db, "get_outfit", lambda _outfit_id: outfit)
    monkeypatch.setattr(phase2_api.db, "live_catalogue", lambda: [too_expensive, replacement])
    monkeypatch.setattr(
        phase2_api.db,
        "get_item_with_tags",
        lambda item_id: replaced if item_id == "belt" else None,
    )
    monkeypatch.setattr(
        phase2_api,
        "rank_items",
        lambda *_args, **_kwargs: [too_expensive, replacement],
    )
    updated = {}

    def update_outfit(_outfit_id, piece_ids, total_gbp):
        updated["piece_ids"] = piece_ids
        updated["total_gbp"] = total_gbp
        outfit.update(piece_ids=piece_ids, total_gbp=total_gbp)

    monkeypatch.setattr(phase2_api.db, "update_outfit_pieces", update_outfit)
    monkeypatch.setattr(phase2_api, "_public_outfit", lambda value: value)
    app = FastAPI()
    app.include_router(phase2_api.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/outfits/outfit/swap", json=SwapRequest(item_id="belt").model_dump()
        )

    assert response.status_code == 200
    assert updated == {
        "piece_ids": ["top", "bottom", "shoes", "replacement"],
        "total_gbp": 95,
    }
