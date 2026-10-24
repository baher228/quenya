from pathlib import Path

from app.config import SHOP_TIERS
from app.sources import catalog

FIXTURES = Path(__file__).parent / "fixtures"


def test_catalog_parses_ssense_gbp_offer_and_image_template(monkeypatch):
    monkeypatch.setattr(
        catalog, "_valid_image",
        lambda url, _page: catalog._image_candidates(url)[0],
    )
    html = (FIXTURES / "catalog_ssense_product.html").read_text()
    records = catalog.parse_product_html(
        html,
        "https://www.ssense.com/en-gb/men/product/example/black-knit-sweater/12345678",
        gender="m",
    )
    assert len(records) == 1
    assert records[0]["title"] == "Black Knit Sweater"
    assert records[0]["price_gbp"] == 425
    assert records[0]["source_url"] == (
        "https://www.ssense.com/en-gb/men/product/example/black-knit-sweater/12345678"
    )
    assert "b_white,c_lpad,g_center,h_960,w_960" in records[0]["image_url"]
    assert records[0]["condition_hint"] == "new"


def test_optional_scrapling_sources_degrade_to_empty_results(monkeypatch):
    monkeypatch.setattr(catalog, "_scrapling_fetchers", lambda: None)
    records, report = catalog.crawl("ssense.com", "m", 4, set(), 0)
    assert records == []
    assert report["error"] == "scrapling_unavailable"

    monkeypatch.setattr(catalog.sport, "_scrapling_fetcher", lambda: None)
    records, report = catalog.sport.search_context(
        ["running jacket"], "running", "m"
    )
    assert records == []
    assert report == {}


def test_catalog_product_group_uses_first_variant_and_aggregate_low_price(monkeypatch):
    monkeypatch.setattr(catalog, "_valid_image", lambda url, _page: url)
    records = catalog.parse_product_html(
        (FIXTURES / "catalog_product_group.html").read_text(),
        "https://www.arket.com/en-gb/product/relaxed-trousers-123456",
        gender="w",
    )
    assert len(records) == 1
    assert records[0]["title"] == "Relaxed wool trousers - navy"
    assert records[0]["price_gbp"] == 89
    assert records[0]["image_url"] == "https://cdn.example.test/navy-trousers.jpg"


def test_catalog_rejects_non_gbp_offer(monkeypatch):
    assert catalog.parse_product_html(
        (FIXTURES / "catalog_non_gbp.html").read_text(),
        "https://www.ssense.com/en-gb/men/product/example/shirt/12345678",
    ) == []


def test_catalog_rejects_uniqlo_reviews_as_product_urls():
    assert not catalog._product_url(
        "https://www.uniqlo.com/uk/en/products/E488758-000/00/reviews", "uniqlo.com"
    )
    assert catalog._product_url(
        "https://www.uniqlo.com/uk/en/products/E486078-000/00", "uniqlo.com"
    )


def test_catalog_accepts_imported_shop_urls_and_tiers():
    examples = (
        (
            "hugoboss.com",
            "https://www.hugoboss.com/uk/equestrian-polo-shirt/"
            "hbeuB8M0601_001.html",
        ),
        (
            "thenorthface.com",
            "https://www.thenorthface.com/en-gb/p/men-211701/"
            "men-s-summit-nuptse-jacket-NF0A8H3D",
        ),
        (
            "stoneisland.com",
            "https://www.stoneisland.com/en-gb/collection/coats-and-jackets/"
            "tear-resistant-down-jacket-L2S154100013S0483V0049.html",
        ),
    )
    for domain, url in examples:
        assert catalog._product_url(url, domain)
    assert SHOP_TIERS["burberry.com"] == "luxury"
    assert SHOP_TIERS["stoneisland.com"] == "luxury"
    assert SHOP_TIERS["hugoboss.com"] == "premium"
    assert SHOP_TIERS["ralphlauren.co.uk"] == "premium"
    assert SHOP_TIERS["lacoste.com"] == "premium"
    assert SHOP_TIERS["thenorthface.com"] == "high_street"


def test_catalog_filters_categories_by_requested_piece_slots():
    urls = catalog.category_urls("ssense.com", "m", ["bottom"])
    assert urls == [
        "https://www.ssense.com/en-gb/men/clothing/trousers",
        "https://www.ssense.com/en-gb/men/clothing/denim",
    ]
