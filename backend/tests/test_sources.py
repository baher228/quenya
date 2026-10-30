import json
from pathlib import Path

from app.sources.retail import parse_extract_result
from app.sources.vinted import normalize_item

FIXTURES = Path(__file__).parent / "fixtures"


def test_real_asos_extract_fixture_drops_page_without_a_product_price():
    fixture = json.loads(
        (FIXTURES / "retail_asos_extract.json").read_text(encoding="utf-8")
    )
    record, reason = parse_extract_result(
        fixture["result"],
        page_url=fixture["page_url"],
        search_title=fixture["search_title"],
    )
    assert record is None
    assert reason == "no_price"


def test_retail_extract_parser_prefers_now_price_and_canonicalizes_asos():
    page_url = (
        "https://www.asos.com/vans/vans-drill-jacket/prd/123456789"
        "?currency=USD#product"
    )
    record, reason = parse_extract_result(
        {
            "url": page_url,
            "raw_content": "# Vans Drill jacket\n\nWas £59.99, Now £39.99",
            "images": [
                "https://images.asos-media.com/products/vans-drill-jacket/123456789-1-black?$n_240w$&wid=44&fit=constrain"
            ],
        },
        page_url=page_url,
    )
    assert reason is None
    assert record is not None
    assert record["title"] == "Vans Drill jacket"
    assert record["brand"] == "Vans"
    assert record["price_gbp"] == 39.99
    assert record["source_url"] == "https://www.asos.com/vans/vans-drill-jacket/prd/123456789"
    assert record["image_url"] == (
        "https://images.asos-media.com/products/vans-drill-jacket/"
        "123456789-1-black?$n_640w$&wid=640&fit=constrain"
    )
    assert record["condition_hint"] == "new"
    assert record["raw_json"]["price_source"] == "tavily_extract_text"
    assert "Now £39.99" in record["raw_json"]["price_snippet"]


def test_asos_parser_requires_a_product_image_not_the_product_page():
    page_url = "https://www.asos.com/prd/123456789"
    result = {
        "raw_content": "# Workwear jacket\n\nNow £39.99",
        "images": [
            page_url,
            "https://images.asos-media.com/products/workwear-jacket/123456789-1-black",
        ],
    }
    record, reason = parse_extract_result(result, page_url=page_url)
    assert reason is None
    assert record is not None
    assert record["image_url"] == (
        "https://images.asos-media.com/products/workwear-jacket/"
        "123456789-1-black?$n_640w$&wid=640&fit=constrain"
    )

    result["images"] = [page_url]
    record, reason = parse_extract_result(result, page_url=page_url)
    assert record is None
    assert reason == "no_product_image"


def test_retail_extract_parser_accepts_other_uk_domains_and_rejects_us_asos():
    record, reason = parse_extract_result(
        {
            "raw_content": "# Linen shirt\n\nNow £34.00",
            "images": ["https://image.hm.com/product-1234567890.jpg"],
        },
        page_url="https://www2.hm.com/en_gb/productpage.1234567890.html?utm_source=test",
        gender="w",
    )
    assert reason is None
    assert record is not None
    assert record["source_url"] == "https://www2.hm.com/en_gb/productpage.1234567890.html"
    assert record["gender_hint"] == "w"
    assert record["price_gbp"] == 34.0

    record, reason = parse_extract_result(
        {"raw_content": "# Jacket\n£50", "images": ["https://images.test/123456789-1.jpg"]},
        page_url="https://www.asos.com/us/vans/jacket/prd/123456789",
        search_title="Jacket | ASOS",
    )
    assert record is None
    assert reason == "non_uk_locale"


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


def test_vinted_normalizer_extracts_brand_from_production_item_box_fixture():
    fixture = json.loads(
        (FIXTURES / "vinted_stussy_brand.json").read_text(encoding="utf-8")
    )

    record = normalize_item(fixture, "stussy top", "w")

    assert record is not None
    assert record["brand"] == "Stüssy"


def test_vinted_normalizer_falls_back_to_item_box_first_line():
    item = {
        "id": 456,
        "title": "Vintage tee",
        "price": 12,
        "url": "/items/456-vintage-tee",
        "photos": [{"url": "https://images.example/tee.jpg"}],
        "item_box": {
            "accessibility_label": "Vintage tee, Condition: Good, Size: M",
            "first_line": "Noah NYC",
        },
    }

    record = normalize_item(item, "vintage tee", "m")

    assert record is not None
    assert record["brand"] == "Noah NYC"


def test_vinted_normalizer_preserves_commas_in_accessibility_brand():
    fixture = json.loads(
        (FIXTURES / "vinted_stussy_brand.json").read_text(encoding="utf-8")
    )
    fixture["item_box"]["accessibility_label"] = (
        "Top, Brand: Atelier, Inc., Condition: Good, Size: M"
    )

    record = normalize_item(fixture, "top", "w")

    assert record is not None
    assert record["brand"] == "Atelier, Inc."


def test_vinted_normalizer_without_item_box_has_no_brand():
    item = {
        "id": 789,
        "title": "Vintage tee",
        "price": 12,
        "url": "/items/789-vintage-tee",
        "photos": [{"url": "https://images.example/tee.jpg"}],
    }

    record = normalize_item(item, "vintage tee", "m")

    assert record is not None
    assert record["brand"] is None
