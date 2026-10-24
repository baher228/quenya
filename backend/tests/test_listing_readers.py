import re
from pathlib import Path

import pytest

from app.sources import catalog
from app.sources.listing_readers import parse_listing

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
