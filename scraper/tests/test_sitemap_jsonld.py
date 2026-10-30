import json
import re
import tomllib
from pathlib import Path

from adapters import shopify, sitemap_jsonld

FIXTURES = Path(__file__).parent / "fixtures"
SHOPS_PATH = Path(__file__).parents[1] / "shops.toml"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FixtureSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.emitted = []
        self.full = False

    def get(self, url, browser=False):
        self.requests.append({"url": url, "status": 200, "browser": browser})
        return self.responses.pop(0), url, None

    def emit(self, rows):
        self.emitted.extend(rows)


def sitemap_shop(**overrides):
    return {
        "key": "test",
        "brand": "Test Brand",
        "adapter": "sitemap_jsonld",
        "host": "example.test",
        "domain": "example.test",
        "default_gender": "m",
        "_category_set": "tees",
        "currency": "GBP",
        "sitemap_url": "https://example.test/sitemap.xml",
        "url_pattern": "tee",
        **overrides,
    }


def test_sitemap_jsonld_keeps_explicit_gbp_product():
    shop = sitemap_shop()
    session = FixtureSession([fixture("sitemap-single.xml"), fixture("sitemap-gbp.html")])

    rows = sitemap_jsonld.scrape(shop, session)

    assert len(rows) == 1
    assert rows[0]["title"] == "Graphic Tee"
    assert rows[0]["brand"] == "Carhartt WIP"
    assert rows[0]["price_gbp"] == 42
    assert rows[0]["image_url_original"] == "https://cdn.example.test/graphic-tee.jpg"
    assert shop["_sitemap_jsonld_report"]["checked"] == 1
    assert shop["_sitemap_jsonld_report"]["kept"] == 1


def test_sitemap_jsonld_converts_eur_from_graph_and_preserves_currency():
    shop = sitemap_shop(currency="EUR", _fx_rate=0.87)
    session = FixtureSession(
        [fixture("sitemap-single.xml"), fixture("sitemap-eur-graph.html")]
    )

    rows = sitemap_jsonld.scrape(shop, session)

    assert len(rows) == 1
    assert rows[0]["title"] == "Artwork Tee"
    assert rows[0]["brand"] == "Our Legacy"
    assert rows[0]["price_gbp"] == 87
    raw = rows[0]["record"]["raw_json"]
    assert raw["original_price"] == 100.0
    assert raw["original_currency"] == "EUR"
    assert raw["fx_rate"] == 0.87


def test_sitemap_jsonld_skips_usd_and_missing_currency():
    shop = sitemap_shop()
    session = FixtureSession(
        [
            fixture("sitemap-currency.xml"),
            fixture("sitemap-usd.html"),
            fixture("sitemap-no-currency.html"),
        ]
    )

    assert sitemap_jsonld.scrape(shop, session) == []
    report = shop["_sitemap_jsonld_report"]
    assert report["checked"] == 2
    assert report["kept"] == 0
    assert report["skipped_currency"] == 2


def test_sitemap_jsonld_skips_out_of_stock_and_uses_open_graph_price():
    stockout_shop = sitemap_shop()
    stockout_session = FixtureSession(
        [fixture("sitemap-single.xml"), fixture("sitemap-out-of-stock.html")]
    )

    assert sitemap_jsonld.scrape(stockout_shop, stockout_session) == []
    assert stockout_shop["_sitemap_jsonld_report"]["out_of_stock"] == 1

    og_shop = sitemap_shop()
    og_session = FixtureSession(
        [fixture("sitemap-single.xml"), fixture("sitemap-og.html")]
    )

    rows = sitemap_jsonld.scrape(og_shop, og_session)

    assert len(rows) == 1
    assert rows[0]["title"] == "OG Graphic Tee"
    assert rows[0]["price_gbp"] == 36
    assert rows[0]["image_url_original"] == "https://cdn.example.test/og-tee.jpg"


def test_sitemap_jsonld_recurses_and_filters_urls():
    shop = sitemap_shop(
        sitemap_url="https://example.test/sitemaps/index.xml",
        url_include="/en-gb/",
        url_pattern="tee",
    )
    session = FixtureSession(
        [
            fixture("sitemap-index.xml"),
            fixture("sitemap-child-index.xml"),
            fixture("sitemap-grandchild.xml"),
            fixture("sitemap-gbp.html"),
        ]
    )

    rows = sitemap_jsonld.scrape(shop, session)

    assert len(rows) == 1
    assert shop["_sitemap_jsonld_report"]["sitemap_requests"] == 3
    assert shop["_sitemap_jsonld_report"]["product_urls"] == 1
    assert session.requests[-1]["url"] == "https://example.test/en-gb/products/graphic-tee"


def test_shopify_stockist_apparel_uses_vendor_brand_and_collection_scope():
    product = {
        "id": 11,
        "title": "Graphic Tee",
        "vendor": "Noah NYC",
        "product_type": "T-Shirts",
        "tags": ["t-shirt"],
        "images": [{"src": "https://cdn.goodhoodstore.com/noah-tee.jpg"}],
        "variants": [
            {
                "id": 101,
                "available": True,
                "price": "48.00",
                "option1": "Default",
            }
        ],
    }
    shop = {
        "key": "goodhood",
        "brand": "Goodhood",
        "host": "goodhoodstore.com",
        "domain": "goodhoodstore.com",
        "adapter": "shopify",
        "currency": "GBP",
        "product_base": "https://goodhoodstore.com",
        "max_pages": 1,
        "collections": [{"handle": "noah"}],
        "brand_from_vendor": True,
        "_category_set": "apparel",
        "default_gender": "m",
    }
    session = FixtureSession(
        [
            fixture("shopify-meta-gbp.json"),
            json.dumps({"products": [product]}),
        ]
    )

    rows = shopify.scrape(shop, session)

    assert len(rows) == 1
    assert rows[0]["brand"] == "Noah NYC"
    assert rows[0]["record"]["source"] == "goodhoodstore.com"
    assert "/collections/noah/products.json" in session.requests[1]["url"]


def test_new_shop_configurations_parse_and_use_supported_adapters():
    shops = tomllib.loads(SHOPS_PATH.read_text(encoding="utf-8"))["shop"]
    by_key = {shop["key"]: shop for shop in shops}

    for key in (
        "carharttwip",
        "ourlegacy",
        "woodwood",
        "gramicciuk",
        "oipolloi",
        "goodhood",
    ):
        assert by_key[key]["enabled"] is True
    assert by_key["carharttwip"]["adapter"] == "sitemap_jsonld"
    assert by_key["carharttwip"]["max_requests"] == 250
    assert by_key["ourlegacy"]["currency"] == "EUR"
    assert by_key["woodwood"]["currency"] == "EUR"
    assert by_key["gramicciuk"]["adapter"] == "shopify"
    assert by_key["oipolloi"]["tee_collections"] == [
        {"handle": "carhartt-wip"},
        {"handle": "gramicci"},
    ]
    assert by_key["oipolloi"]["collections"] == [
        {"handle": "carhartt-wip"},
        {"handle": "gramicci"},
    ]
    assert by_key["goodhood"]["tee_collections"] == [
        {"handle": "noah"},
        {"handle": "awake-ny"},
        {"handle": "cav-empt"},
        {"handle": "wacko-maria"},
    ]
    assert by_key["goodhood"]["collections"] == [
        {"handle": "noah"},
        {"handle": "awake-ny"},
        {"handle": "cav-empt"},
        {"handle": "wacko-maria"},
    ]
    assert by_key["oipolloi"]["brand_from_vendor"] is True
    assert by_key["goodhood"]["brand_from_vendor"] is True
    assert by_key["dailypaper"]["enabled"] is True
    assert by_key["dailypaper"]["domain"] == "dailypaperclothing.com"
    assert by_key["stoneisland_jsonld"]["enabled"] is False


def test_carhartt_url_pattern_matches_tee_slugs_without_short_or_pant_false_positives():
    shops = tomllib.loads(SHOPS_PATH.read_text(encoding="utf-8"))["shop"]
    by_key = {shop["key"]: shop for shop in shops}
    pattern = re.compile(by_key["carharttwip"]["url_pattern"], re.IGNORECASE)

    assert pattern.search("/en-gb/p/w-l-s-blackletter-script-waffle-t-s-wax-black-241")
    assert pattern.search("/en-gb/p/s-s-cardona-pocket-t-shirt-cardona-stripe-natural")
    assert not pattern.search("/en-gb/p/flint-short-wall-garment-dyed-162")
    assert not pattern.search("/en-gb/p/brady-pant-shungite-stone-washed-1128")
