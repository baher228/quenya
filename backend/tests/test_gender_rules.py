import pytest

from app.gender_rules import text_gender
from app.rank import eligible


@pytest.mark.parametrize(
    ("title", "product_type", "url", "expected"),
    [
        ("Women's Straight Cotton Dress - Red", "", "", "womenswear"),
        ("Maxi Skirt - Quilted Navy", "", "", "womenswear"),
        ("Dress Shirt - White", "", "", None),
        ("Leather Dress Shoes", "", "", None),
        ("Mens Long Sleeve T-Shirt", "", "", "menswear"),
        (
            "Recycled Nimbus Hooded Jacket",
            "",
            "https://finisterre.com/products/mens-recycled-nimbus-hooded-jacket-black",
            "menswear",
        ),
        ("UNISEX CREW BLACK", "", "", "unisex"),
        ("Essential T-Shirt", "Men's T-Shirts", "", "menswear"),
        ("Men's and Women's Socks", "", "", None),
        ("Women's Short PJ Trousers", "", "", "womenswear"),
    ],
)
def test_text_gender(title, product_type, url, expected):
    assert text_gender(title, product_type, url) == expected


def _item(title):
    return {
        "id": "item-1",
        "title": title,
        "source_url": "https://shop.example/products/item-1",
        "availability": "InStock",
        "price_gbp": 50,
        "tags": {"gender": "menswear", "condition": "new", "slot": "top"},
    }


def _brief(gender):
    return {"gender": gender, "budget_gbp": 100}


def _profile():
    return {"condition_preference": "both", "price_ceiling_gbp": 100}


def test_eligible_rejects_text_gender_contradicting_menswear_brief():
    assert not eligible(
        _item("Women's Ava Short Sleeve Shirt"),
        _brief("menswear"),
        _profile(),
    )
    assert eligible(_item("Men's Ava Shirt"), _brief("menswear"), _profile())


def test_eligible_leaves_both_gender_briefs_unchanged():
    assert eligible(
        _item("Women's Ava Short Sleeve Shirt"),
        _brief("both"),
        _profile(),
    )
