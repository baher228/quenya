import re
from pathlib import Path

import pytest

from app.sources import catalog
from app.sources.listing_readers import (
    _clean_stories_title,
    _gender_mismatch,
    infer_category,
    parse_listing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "listings"
EXPECTED = [
    (
        "cos", "cos.com", "m", 5, "BRUSHED-WOOL POLO SHIRT", 85.0,
        "https://media.cos.com/assets/001/45/80/4580da64a13bd0b07bf76b5067c0ad37bf957ce2_xxl-1.jpg",
        "https://www.cos.com/en-gb/men/menswear/knitwear/jumpers/product/"
        "brushed-wool-polo-shirt-dark-red-mlange-1302327005",
    ),
    (
        "arket", "arket.com", "m", 5, "Heavy Knit Wool Jumper", 75.0,
        "https://media.arket.com/assets/006/35/6f/356f5adec359b5b0ede9aea1fafc59889513f068_xxl-1.jpg",
        "https://www.arket.com/en-gb/product/heavy-knit-wool-jumper-dark-blue-1296629004",
    ),
    (
        "otherstories", "stories.com", "w", 5, "Mohair-Blend Jacquard Jumper", 119.0,
        "https://media.stories.com/assets/005/47/cd/47cd0919342ae1d73811a1ec4e9645f5e966d4c7_xxl-1.jpg",
        "https://www.stories.com/en-gb/product/"
        "mohair-blend-jacquard-jumper-creambrown-1364484001",
    ),
    (
        "barbour", "barbour.com", "m", 5,
        "Fair Isle Made in Scotland Crew-Neck Jumper Grey Marl", 229.0,
        "https://www.barbour.com/dw/image/v2/blcl_prd/on/demandware.static/-/"
        "Sites-master-catalog/default/dw97cf5ad9/images/MKN1764GY52/"
        "MKN1764GY52_01front.jpg?q=70&strip=false&sw=1000",
        "https://www.barbour.com/gb/fair-isle-made-in-scotland-crew-neck-jumper-MKN1764GY52.html",
    ),
    (
        "charlestyrwhitt", "charlestyrwhitt.com", "m", 5,
        "Ultimate Non-Iron Chinos - Navy", 79.95,
        "https://www.charlestyrwhitt.com/dw/image/v2/AAWJ_PRD/on/demandware.static/-/"
        "Sites-ctshirts-master/default/dw444eba20/TRC/TRC0274FRN/"
        "TRC0274FRN_MODEL_FULL_FRONT_01.jpg?cx=750&cy=950&cw=750&ch=950?sw=750&sm=fit",
        "https://www.charlestyrwhitt.com/uk/ultimate-non-iron-chinos---navy/"
        "TRC0274FRN.html",
    ),
    (
        "johnsmedley", "johnsmedley.com", "m", 5,
        "Standard Fit Lundy Extra Fine Merino Wool Jumper", 235.0,
        "https://www.datocms-assets.com/170444/1789388814-78_05e11e6fee-"
        "lundymid-1-original.jpg?q=80&auto=format,compress&w=714&h=952&fit=max",
        "https://www.johnsmedley.com/uk/lundy-midnight",
    ),
    (
        "asos", "asos.com", "m", 5,
        "CAPO egyptian 100% cotton quarter zip polo in black", 45.0,
        "https://images.asos-media.com/products/capo-egyptian-100-cotton-quarter-zip-"
        "polo-in-black/211526090-1-black/?$n_640w$&wid=640&fit=constrain",
        "https://www.asos.com/capo/capo-egyptian-100-cotton-quarter-zip-polo-in-black/"
        "prd/211526090",
    ),
    (
        "mango", "mango.com", "w", 5, "Faux-leather flared trousers", 49.99,
        "https://media.mango.com/is/image/punto/37045163-32-007?wid=2048&hei=2867&fit=crop",
        "https://shop.mango.com/gb/en/p/women/trousers/party/"
        "faux-leather-flared-trousers/37045163/32/00",
    ),
]


@pytest.mark.parametrize(
    (
        "shop", "domain", "gender", "count", "title", "price", "image", "url",
    ),
    EXPECTED,
)
def test_listing_reader_first_record(
    monkeypatch, shop, domain, gender, count, title, price, image, url,
):
    monkeypatch.setattr(
        catalog, "_valid_image",
        lambda candidate, _page: candidate if candidate.startswith("https://") else None,
    )
    html = (FIXTURES / f"{shop}.html").read_text()
    page_url = re.search(r"fixture-page-url: ([^\s]+)", html).group(1)
    records = parse_listing(html, page_url, domain, gender)

    assert len(records) == count
    assert records[0]["title"] == title
    assert records[0]["price_gbp"] == price
    assert records[0]["image_url"] == image
    assert records[0]["source_url"] == url
    assert records[0]["source"] == domain
    assert records[0]["condition_hint"] == "new"


@pytest.mark.parametrize(
    "tile",
    [
        '<a href="/gb/jumper.html">Wool jumper $85.00<img src="https://img.example/j.jpg"></a>',
        '<a href="/gb/jumper.html">Wool jumper<img src="https://img.example/j.jpg"></a>',
    ],
)
def test_listing_reader_rejects_non_gbp_and_missing_price(monkeypatch, tile):
    monkeypatch.setattr(catalog, "_valid_image", lambda url, _page: url)
    html = f"<main>{tile}</main>"
    assert parse_listing(
        html, "https://www.barbour.com/gb/knitwear", "barbour.com", "m"
    ) == []


def test_listing_reader_ignores_unknown_domains():
    assert parse_listing(
        '<a href="/product"><img src="https://img.example/j.jpg"></a>',
        "https://example.com/catalog",
        "example.com",
        "m",
    ) == []


@pytest.mark.parametrize(
    ("domain", "url", "expected"),
    [
        ("cos.com", EXPECTED[0][7], "COS"),
        ("arket.com", EXPECTED[1][7], "ARKET"),
        ("stories.com", EXPECTED[2][7], "& Other Stories"),
        ("barbour.com", EXPECTED[3][7], "Barbour"),
        ("johnsmedley.com", EXPECTED[5][7], "John Smedley"),
        ("charlestyrwhitt.com", EXPECTED[4][7], "Charles Tyrwhitt"),
        ("asos.com", EXPECTED[6][7], "ASOS"),
        ("mango.com", EXPECTED[7][7], "Mango"),
    ],
)
def test_catalog_record_uses_shop_brand_fallback(monkeypatch, domain, url, expected):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    product = {
        "name": "Sample Wool Jumper",
        "url": url,
        "image": "https://img.example/item.jpg",
        "offers": {"priceCurrency": "GBP", "price": 75},
    }
    record = catalog._record(product, product, url, domain, "m", "test")
    assert record["brand"] == expected


def test_catalog_record_prefers_asos_brand_name(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    url = EXPECTED[6][7]
    product = {
        "name": "Quarter Zip Polo",
        "brandName": "CAPO",
        "url": url,
        "image": "https://img.example/item.jpg",
        "offers": {"priceCurrency": "GBP", "price": 45},
    }
    record = catalog._record(product, product, url, "asos.com", "m", "test")
    assert record["brand"] == "CAPO"


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("Curved Leather Bag", "accessory"),
        ("Leather Ankle Boots", "shoes"),
        ("Belted Leather Skirt", "bottom"),
        ("Wool-Cotton V-Neck Jumper", "knitwear"),
        ("Non-Iron Shirt", "top"),
        ("Relaxed Chinos", "bottom"),
        ("Wool Blend Coat", "outerwear"),
        ("Merino Wool Dress", "dress"),
    ],
)
def test_category_inference_uses_product_title(title, category):
    assert infer_category(title, "https://www.stories.com/en-gb/clothing/knitwear/") == category


def test_category_inference_falls_back_to_listing_url():
    assert infer_category(
        "Unclassified item", "https://www.cos.com/en-gb/men/knitwear"
    ) == "knitwear"


def test_stories_title_cleanup_removes_tile_ui_text():
    assert _clean_stories_title(
        "chevron-right chevron-left plus favourite-inactive "
        "Boxy Knit Jumper + 5 New Cotton-wool"
    ) == "Boxy Knit Jumper"


def test_stories_uses_structured_title_by_product_id(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    html = """
    <script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"products":[{"name":"Boxy Knit Jumper","id":"1322087001",
    "priceAsNumber":57,"images":["https://media.stories.com/item.jpg"]}]}}}
    </script>
    <a href="/en-gb/product/boxy-knit-jumper-dark-grey-1322087001">
      <span>
        chevron-right chevron-left plus favourite-inactive Boxy Knit Jumper
        + 5 New Cotton-wool
      </span>
      £57 <img src="https://media.stories.com/item.jpg">
    </a>
    """
    records = parse_listing(
        html, "https://www.stories.com/en-gb/clothing/knitwear/",
        "stories.com", "w",
    )
    assert len(records) == 1
    assert records[0]["title"] == "Boxy Knit Jumper"
    assert records[0]["brand"] == "& Other Stories"


def test_barbour_itemlist_excludes_non_listing_product_links(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    html = """
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[{"@type":"ListItem",
     "url":"https://www.barbour.com/gb/fair-isle-jumper-MKN1764GY52.html"}]}
    </script>
    <a href="/gb/fair-isle-jumper-MKN1764GY52.html">
      Fair Isle Jumper £229 <img src="https://img.example/product.jpg">
    </a>
    <a href="/gb/unlisted-jumper-ABC123.html">
      Mens Home £50 <img src="https://img.example/navigation.jpg">
    </a>
    """
    records = parse_listing(
        html, "https://www.barbour.com/gb/mens/clothing/knitwear",
        "barbour.com", "m",
    )
    assert len(records) == 1
    assert records[0]["title"] == "Fair Isle Jumper"


def test_john_smedley_title_omits_colour_count_and_hover_duplicate(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    html = """
    <a data-product-card="" href="/uk/lundy-midnight">
      <span class="functional-f2">Standard Fit</span>
      <span class="functional-f1">Lundy</span>
      <span class="functional-f1">Extra Fine Merino Wool Jumper</span>
      <span class="functional-f2">20 colours</span>
      <span class="functional-f2">Midnight</span>
      <span class="functional-f1">Extra Fine Merino Wool Jumper</span>
      <span class="functional-f2">Midnight</span>
      £235 <img src="https://img.example/lundy.jpg">
    </a>
    """
    records = parse_listing(
        html, "https://www.johnsmedley.com/uk/mens",
        "johnsmedley.com", "m",
    )
    assert len(records) == 1
    assert records[0]["title"] == "Lundy Extra Fine Merino Wool Jumper"


def test_john_smedley_drops_tile_with_explicit_other_gender(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    html = """
    <script type="application/ld+json">
    {"@type":"Product","name":"Womens Wool Jumper","url":"/uk/womens-wool-jumper",
     "gender":"women","image":"https://img.example/jumper.jpg",
     "offers":{"priceCurrency":"GBP","price":75}}
    </script>
    <a data-product-card="" href="/uk/womens-wool-jumper">
      <span class="functional-f1">Womens Wool Jumper</span>
      £75 <img src="https://img.example/jumper.jpg">
    </a>
    """
    records = parse_listing(
        html, "https://www.johnsmedley.com/uk/mens",
        "johnsmedley.com", "m",
    )
    assert records == []


def test_generic_tile_title_removes_asos_view_label(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda image, _page: image)
    html = """
    <a href="https://www.asos.com/capo/capo-quarter-zip-polo/prd/211526090">
      CAPO Quarter Zip Polo - view 1, Price £45
      <img alt="CAPO Quarter Zip Polo - view 1" src="https://img.example/polo.jpg">
    </a>
    """
    records = parse_listing(html, "https://www.asos.com/men/jumpers", "asos.com", "m")
    assert records[0]["title"] == "CAPO Quarter Zip Polo"


def test_john_smedley_payload_gender_scope():
    assert _gender_mismatch({"categoryUri": "womenswear/knitwear"}, "m")
    assert not _gender_mismatch({"categoryUri": "menswear/knitwear"}, "m")
