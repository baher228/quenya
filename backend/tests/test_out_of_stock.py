import json

from app import db
from app.sources import STOCK_PHRASE, catalog, is_unavailable, sport
from app.sources.retail import parse_extract_result


def test_stock_status_helpers():
    for availability in (
        "OutOfStock",
        "https://schema.org/OutOfStock",
        "https://schema.org/soldout",
        "https://schema.org/Discontinued",
    ):
        assert is_unavailable(availability)

    for availability in (
        None,
        "InStock",
        "https://schema.org/InStock",
        "https://schema.org/LimitedAvailability",
        "https://schema.org/PreOrder",
        "https://schema.org/BackOrder",
    ):
        assert not is_unavailable(availability)

    for phrase in (
        "out of stock",
        "SOLD OUT",
        "this item is sold out",
        "no longer available",
        "currently unavailable",
    ):
        assert STOCK_PHRASE.fullmatch(phrase.strip())
    assert not STOCK_PHRASE.fullmatch("Free returns unless sold out")


def _retail_result(content):
    return parse_extract_result(
        {
            "raw_content": content,
            "images": [
                "https://images.asos-media.com/products/test/"
                "123456789-1-black"
            ],
        },
        page_url="https://www.asos.com/prd/123456789",
    )


def test_retail_parser_rejects_stock_banner_headings_and_lines():
    record, reason = _retail_result("# Out of stock\n\n£39.99")
    assert record is None
    assert reason == "out_of_stock"

    record, reason = _retail_result(
        "# Work jacket\n\nSold out\n\nNow £39.99"
    )
    assert record is None
    assert reason == "out_of_stock"


def test_retail_parser_allows_stock_words_in_a_normal_sentence():
    record, reason = _retail_result(
        "# Work jacket\n\nFree returns unless sold out\n\nNow £39.99"
    )
    assert reason is None
    assert record is not None
    assert record["title"] == "Work jacket"


def test_catalog_drops_unavailable_offers_and_keeps_limited_availability(
    monkeypatch,
):
    monkeypatch.setattr(catalog, "_valid_image", lambda url, _page: url)
    page_url = "https://www.arket.com/en-gb/product/relaxed-trousers-123456"
    item = {
        "name": "Relaxed trousers",
        "image": "https://cdn.example.test/trousers.jpg",
        "offers": [
            {
                "@type": "Offer",
                "priceCurrency": "GBP",
                "price": "89.00",
                "availability": "https://schema.org/OutOfStock",
            }
        ],
    }
    assert catalog._record(item, {}, page_url, "arket.com", "w", "test") is None

    item["offers"] = [
        {
            "@type": "Offer",
            "priceCurrency": "GBP",
            "price": "89.00",
            "availability": "https://schema.org/LimitedAvailability",
        }
    ]
    record = catalog._record(item, {}, page_url, "arket.com", "w", "test")
    assert record["availability"] == "https://schema.org/LimitedAvailability"

    item["offers"] = [
        {
            "@type": "Offer",
            "priceCurrency": "GBP",
            "price": "89.00",
            "availability": "https://schema.org/OutOfStock",
        },
        {
            "@type": "Offer",
            "priceCurrency": "GBP",
            "price": "95.00",
            "availability": "https://schema.org/LimitedAvailability",
        },
    ]
    record = catalog._record(item, {}, page_url, "arket.com", "w", "test")
    assert record["price_gbp"] == 95
    assert record["availability"] == "https://schema.org/LimitedAvailability"


def _sport_html(offers):
    product = {
        "@type": "Product",
        "name": "Training jacket",
        "image": "https://cdn.example.test/jacket.jpg",
        "offers": offers,
    }
    return (
        '<script type="application/ld+json">'
        f"{json.dumps(product)}</script>"
    )


def test_sport_parser_skips_out_of_stock_and_keeps_available_offers():
    page_url = "https://www.underarmour.co.uk/en-gb/p/123456789"
    out_of_stock = {
        "@type": "Offer",
        "priceCurrency": "GBP",
        "price": "55.00",
        "availability": "https://schema.org/OutOfStock",
    }
    record, reason = sport.parse_product_html(
        _sport_html(out_of_stock), page_url
    )
    assert record is None
    assert reason == "out_of_stock"

    limited = {
        **out_of_stock,
        "availability": "https://schema.org/LimitedAvailability",
    }
    record, reason = sport.parse_product_html(_sport_html(limited), page_url)
    assert reason is None
    assert record["availability"] == "https://schema.org/LimitedAvailability"

    record, reason = sport.parse_product_html(
        _sport_html([out_of_stock, limited]), page_url
    )
    assert reason is None
    assert record["availability"] == "https://schema.org/LimitedAvailability"

    record, reason = sport.parse_product_html(
        _sport_html([out_of_stock, out_of_stock]), page_url
    )
    assert record is None
    assert reason == "out_of_stock"


def test_list_items_filters_out_unavailable_products(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "catalog.sqlite")
    records = []
    availability = {
        "out-of-stock": "https://schema.org/OutOfStock",
        "sold-out": "https://schema.org/SoldOut",
        "discontinued": "https://schema.org/Discontinued",
        "none": None,
        "in-stock": "InStock",
        "limited": "https://schema.org/LimitedAvailability",
    }
    for item_id, status in availability.items():
        record = {
            "id": item_id,
            "source": "test",
            "source_url": f"https://example.test/{item_id}",
            "title": item_id,
            "price_gbp": 20,
            "image_url": f"https://example.test/{item_id}.jpg",
        }
        if status is not None:
            record["availability"] = status
        records.append(record)
    assert db.insert_items(records) == len(records)
    for record in records:
        db.save_tags(record["id"], {"slot": "top"}, None)

    assert {
        item["id"] for item in db.list_items(limit=20)
    } == {"none", "in-stock", "limited"}
