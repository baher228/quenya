import json
from pathlib import Path

from app.sources.retail import parse_product_html
from app.sources.vinted import normalize_item

FIXTURES = Path(__file__).parent / "fixtures"


def test_retail_jsonld_parser_extracts_complete_gbp_product():
    html = (FIXTURES / "retail_product.html").read_text(encoding="utf-8")
    record = parse_product_html(html, "https://www.example-retailer.co.uk/item")
    assert record == {
        "title": "Cotton chore jacket",
        "brand": "Example",
        "price_gbp": 89.0,
        "image_url": "https://cdn.example.co.uk/chore-jacket.jpg",
        "source_url": "https://www.example-retailer.co.uk/products/chore-jacket-12345",
        "availability": "InStock",
        "raw_json": {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Cotton chore jacket",
            "brand": {"@type": "Brand", "name": "Example"},
            "image": ["https://cdn.example.co.uk/chore-jacket.jpg"],
            "offers": {
                "@type": "Offer",
                "price": "89.00",
                "priceCurrency": "GBP",
                "availability": "https://schema.org/InStock",
            },
        },
    }


def test_vinted_normalizer_uses_catalog_fixture():
    fixture = json.loads((FIXTURES / "vinted_item.json").read_text(encoding="utf-8"))
    record = normalize_item(fixture, fixture["q"], "w")
    assert record is not None
    assert record["id"] == "vinted:10494868398"
    assert record["source"] == "vinted"
    assert record["source_url"] == fixture["url"]
    assert record["title"] == fixture["title"]
    assert record["price_gbp"] == 50.0
    assert record["image_url"].startswith("https://images1.vinted.net/")
    assert record["condition_hint"] == "second-hand"
    assert record["gender_hint"] == "w"
