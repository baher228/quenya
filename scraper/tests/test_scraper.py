import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
import scrape
import store
from adapters import burberry, common, jsonld, nike, sfcc, shopify, ssense, tiles
from adapters.common import ShopSession, make_row, unique_rows
from adapters.tees import tee_category, tee_gender
from scrape import _images_broken, _shard_shops, _shop_filter, split_batches

FIXTURES = Path(__file__).parent / "fixtures"
WORKER_KEYS = {
    "url",
    "source",
    "title",
    "brand",
    "price_gbp",
    "gender",
    "category",
    "condition",
    "image_url_original",
    "record",
}
RECORD_KEYS = {
    "id",
    "source",
    "source_url",
    "title",
    "brand",
    "price_gbp",
    "size",
    "description",
    "image_url",
    "gender_hint",
    "condition_hint",
    "availability",
    "query",
    "raw_json",
}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.skipped = []
        self.emitted = []
        self.on_emit = None

    def get(self, url, browser=False):
        self.requests.append({"url": url, "browser": browser})
        response = self.responses.pop(0)
        if callable(response):
            response = response(url)
        return response, url, None

    def emit(self, rows):
        self.emitted.extend(rows)
        if self.on_emit:
            self.on_emit(rows)


def shop(key="example", brand="Example", adapter="jsonld", domain="example.test", **extra):
    return {
        "key": key,
        "brand": brand,
        "adapter": adapter,
        "domain": domain,
        "host": domain,
        "default_gender": "m",
        **extra,
    }


def assert_worker_row(row):
    from app.supabase_import import _is_stock_row

    assert set(row) == WORKER_KEYS
    assert set(row["record"]) == RECORD_KEYS
    assert _is_stock_row(row) is False


def test_shopify_requires_gbp_and_reads_available_minimum_with_pagination():
    meta = fixture("shopify-meta-gbp.json")
    product_page = fixture("shopify-products.json")
    fake = FakeSession([meta, product_page, '{"products": []}'])
    config = shop("test", "Test", "shopify", "example-shop.test", max_pages=4)

    rows = shopify.scrape(config, fake)

    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Wool Shirt"
    assert row["price_gbp"] == 75
    assert row["image_url_original"].startswith("https://")
    assert row["record"]["raw_json"]["compare_at_price"] == 90
    assert row["gender"] == "m"
    assert len(fake.requests) == 3
    assert "page=2" in fake.requests[-1]["url"]
    assert_worker_row(row)


def test_shopify_converts_eur_and_preserves_original_price_metadata():
    fake = FakeSession(
        [fixture("shopify-meta-eur.json"), fixture("shopify-products.json")]
    )
    config = shop(
        "test",
        "Test",
        "shopify",
        "example-shop.test",
        currency="EUR",
        _fx_rate=0.87,
        max_pages=1,
    )

    rows = shopify.scrape(config, fake)

    assert len(rows) == 1
    assert rows[0]["price_gbp"] == 65
    raw = rows[0]["record"]["raw_json"]
    assert raw["original_price"] == 75.0
    assert raw["original_currency"] == "EUR"
    assert raw["fx_rate"] == 0.87
    assert raw["price_source"] == "shopify_variant_eur_fx"


def test_prepare_fx_rates_fetches_once_and_leaves_gbp_shops_unchanged(monkeypatch):
    eur_shops = [
        shop("eur-a", adapter="shopify", currency="EUR"),
        shop("eur-b", adapter="shopify", currency="EUR"),
        shop("eur-sitemap", adapter="sitemap_jsonld", currency="EUR"),
    ]
    gbp_shop = shop("gbp", adapter="shopify")
    calls = []
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"rates": {"GBP": 0.86}},
    )

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return response

    monkeypatch.setattr(scrape.httpx, "get", fake_get)
    scrape._prepare_fx_rates([*eur_shops, gbp_shop])

    assert calls == [("https://api.frankfurter.app/latest?from=EUR&to=GBP", 10)]
    assert [shop_config["_fx_rate"] for shop_config in eur_shops] == [
        0.86,
        0.86,
        0.86,
    ]
    assert "_fx_rate" not in gbp_shop


def test_fx_fetch_failure_skips_eur_shop(monkeypatch):
    config = shop("eur", adapter="shopify", currency="EUR")

    def fail_get(_url, timeout):
        raise ValueError("rate unavailable")

    monkeypatch.setattr(scrape.httpx, "get", fail_get)
    scrape._prepare_fx_rates([config])
    fake = FakeSession([fixture("shopify-meta-eur.json")])

    assert shopify.scrape(config, fake) == []
    assert config["_status"] == "fx_unavailable"
    assert fake.requests == []


def test_shopify_accessory_mode_filters_exclusions_and_keeps_apparel_unchanged():
    product_page = fixture("shopify-accessories.json")
    accessories = shop(
        "test",
        "Test",
        "shopify",
        "example-shop.test",
        max_pages=1,
        _category_set="accessories",
    )
    rows = shopify.scrape(
        accessories,
        FakeSession([fixture("shopify-meta-gbp.json"), product_page]),
    )

    assert [row["title"] for row in rows] == [
        "Leather Belt",
        "Small Tote Bag",
        "Ribbed Cotton Socks",
        "Gold Hoop Earrings",
        "Women's Leather Belt",
    ]
    assert [row["record"]["raw_json"]["accessory_slot"] for row in rows] == [
        "accessory",
        "bag",
        "accessory",
        "accessory",
        "accessory",
    ]
    assert rows[-1]["gender"] == "w"
    assert rows[0]["gender"] is None
    assert rows[0]["record"]["gender_hint"] is None
    assert rows[3]["record"]["raw_json"]["accessory_category"] == "earrings"
    assert all(row["record"]["raw_json"]["category_set"] == "accessories" for row in rows)
    for row in rows:
        assert_worker_row(row)
    assert common.accessory_gender_for_shop(
        {"gender_rule": "selfportrait", "accessory_default_gender": "w"},
        "Leather Belt",
    ) == "womenswear"

    women_accessories = dict(accessories, gender_rule="women")
    women_rows = shopify.scrape(
        women_accessories,
        FakeSession([fixture("shopify-meta-gbp.json"), product_page]),
    )
    assert women_rows[0]["title"] == "Leather Belt"
    assert women_rows[0]["gender"] == "w"
    assert women_rows[0]["record"]["gender_hint"] == "w"

    apparel = shop("test", "Test", "shopify", "example-shop.test", max_pages=1)
    apparel_rows = shopify.scrape(
        apparel,
        FakeSession([fixture("shopify-meta-gbp.json"), product_page]),
    )
    assert [row["title"] for row in apparel_rows] == ["Shampoo Gift Set"]
    assert all(common.accessory_kind(row["title"]) is None for row in apparel_rows)


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("Logo Tee", "t-shirt"),
        ("Heavyweight T-Shirt", "t-shirt"),
        ("Dragon LS Tee", "long sleeve t-shirt"),
        ("Pocket T Shirt", "t-shirt"),
        ("Ringer Tee", "t-shirt"),
        ("Raglan Tee", "t-shirt"),
        ("Baby Tee", "t-shirt"),
        ("Beastie Boys Tee", "t-shirt"),
        ("Merino Running Tee", "t-shirt"),
        ("Polo Ralph Lauren Jersey T-Shirt", "t-shirt"),
    ],
)
def test_tee_category_accepts_tshirts(title, category):
    assert tee_category(title) == category


@pytest.mark.parametrize(
    "title",
    [
        "Loopback Sweatshirt",
        "Logo Hoodie",
        "Oxford Shirt",
        "Ribbed Vest",
        "Tank Top",
        "T-Shirt Dress",
        "Kids Logo Tee",
        "3 Pack Boxer",
        "Muscle Tee",
        "Tee + Vinyl Bundle",
        "Gift Card",
    ],
)
def test_tee_category_rejects_non_tshirts(title):
    assert tee_category(title) is None


def test_tee_category_accepts_polos_and_keeps_polo_ralph_lauren_tees():
    assert tee_category("Men's Piqué Polo Shirt") == "polo shirt"
    assert tee_category("Classic Cotton", "Polo Shirts") == "polo shirt"
    assert tee_category("Classic Cotton", tags=["category: polo shirts"]) == "polo shirt"
    assert tee_category("Polo Ralph Lauren Jersey T-Shirt") == "t-shirt"


def test_tee_category_supports_product_type_and_tag_evidence():
    assert tee_category("Logo Longsleeve", "T-Shirts") == "long sleeve t-shirt"
    assert tee_category("Logo Longsleeve", "Sweatshirts") is None
    assert tee_category("Logo Longsleeve", tags=["category: long sleeve t-shirts"]) == (
        "long sleeve t-shirt"
    )
    assert tee_category("Logo Longsleeve", "[Clothing] Cotton Tees and Hoodies") is None
    assert tee_category("V Neck Top", "T-Shirts") is None
    assert tee_category("V Neck Top", tags=["category: T-Shirts"]) is None
    assert tee_category("CB Henley", "T-Shirts") is None
    assert tee_category("Football Jersey", "T-Shirts") is None
    assert tee_category("Rugby Top", "T-Shirts") is None
    assert tee_category("Mesh Top", "T-Shirts") is None
    assert tee_category("V Neck Top T-Shirt") == "t-shirt"


@pytest.mark.parametrize(
    "title",
    [
        "Top",
        "Tops",
        "Henley",
        "Jersey",
        "Turtle Neck",
        "Turtleneck",
        "Roll Neck",
        "Rollneck",
        "Funnel Neck",
        "Funnelneck",
        "Mock Neck",
        "Mockneck",
        "Blouse",
        "Bandeau",
        "Peplum",
        "Wrap",
        "Rugby",
        "Mesh",
        "Bodycon",
        "Corset",
        "Bustier",
        "Crop",
    ],
)
def test_tee_category_rejects_non_tee_title_path_titles(title):
    assert tee_category(title, "T-Shirts") is None


@pytest.mark.parametrize(
    ("title", "product_type", "tags"),
    [
        ("HUF 89 Pullover Windshirt Black", "T-Shirt", ["t-shirt"]),
        ("Former Oscillating Hood Black", "T-Shirt", ["t-shirt"]),
        ("Spitfire Babygrow Blue", "T-Shirt", ["t-shirt"]),
        (
            "Columbia Women's Brea Falls Woven Short Sleeve - Safari",
            "Short Sleeve T-Shirts",
            ["Women's T-Shirts"],
        ),
        ("Trojan Racing Stripe Fine Gauge Turtle Ecru", "T Shirts", ["T Shirts"]),
        ("W ORTank Cropped - Harbor", "ORTank", ["t-shirts"]),
        ("Leti Denim Jeans - blue", "Denim - Jeans", ["t-shirt"]),
        ("aries end. moto-dodgem scarf - Black", "Accessories - Scarves", ["t-shirt"]),
        ("Aries x Umbro Training Pant - Black / White", "Jersey - Tees LS", ["t-shirt"]),
    ],
)
def test_tee_category_rejects_non_tee_garments_from_title_or_product_type(
    title, product_type, tags
):
    assert tee_category(title, product_type, tags) is None


@pytest.mark.parametrize(
    ("title", "product_type"),
    [
        ("Dime MTL Kids T-Shirt Canyon Brown", "T-Shirts"),
        ("Dime MTL Classic Skull Kids T Shirt White", "T-Shirts"),
        ("Weekend Offender Saturdays Kids Graphic Tee White", "T Shirts"),
        ("Toddler T-shirt", "Kidswear"),
    ],
)
def test_tee_category_rejects_kids_and_toddlers_anywhere_in_title(title, product_type):
    assert tee_category(title, product_type) is None


@pytest.mark.parametrize(
    ("title", "product_type", "tags", "category"),
    [
        ("Tanker T-Shirt - White", "T-Shirts", (), "t-shirt"),
        ("April AP Kid T-Shirt - Brown", "T-Shirts", (), "t-shirt"),
        ("Hockey Car Kid Tee Green", "T-Shirts", (), "t-shirt"),
        (
            "Vintage Kidrobot Limited Edition Bones Heavyweight Crewneck T-Shirt - XXL",
            "T-Shirts",
            (),
            "t-shirt",
        ),
        (
            "Vintage Polo Ralph Lauren Big Pony Heavyweight Single Stitch Crewneck T-Shirt - L",
            "T-Shirts",
            (),
            "t-shirt",
        ),
        (
            (
                "Vintage Polo Ralph Lauren Women's Heavyweight Striped Woven Crewneck Henley "
                "T-Shirt - L"
            ),
            "T-Shirts",
            (),
            "t-shirt",
        ),
        ("W DLYTShirt - Dusty Turquoise", "W DLYTshirt", ["t-shirts"], "t-shirt"),
        (
            "Men's Eco Tech T | Yellow",
            "August 26 launches",
            ["t-shirt"],
            "t-shirt",
        ),
        ("Men's Breton - Navy/Ecru", "LS T-Shirts", ["T-Shirts"], "t-shirt"),
        (
            "WINNERS LONG SLEEVE - WASHED YELLOW",
            "Long Sleeve T-Shirts",
            (),
            "long sleeve t-shirt",
        ),
        ("Kid T-Shirt", "T-Shirts", (), "t-shirt"),
    ],
)
def test_tee_category_keeps_valid_garment_words_and_singular_kid(
    title, product_type, tags, category
):
    assert tee_category(title, product_type, tags) == category


@pytest.mark.parametrize(
    ("title", "product_type", "tags"),
    [
        ("Logo Tee", "Boy's T-Shirts", ()),
        ("Logo Tee", "Girl's T-Shirts", ()),
        ("Logo Tee", "Baby T-Shirts", ()),
        ("Logo Tee", "T-Shirts", ("Gender_Boys",)),
        ("Logo Tee", "T-Shirts", ("Gender_Girls",)),
        ("Boy's Logo Tee", "T-Shirts", ()),
        ("Girl's Logo Tee", "T-Shirts", ()),
        ("Kids Logo Tee", "T-Shirts", ()),
        ("Youth Logo Tee", "T-Shirts", ()),
        ("3 PACK T-SHIRT", "", ()),
        ("Big John 2-Pack T-Shirt", "", ()),
        ("Pack of 3 Muscle Fit T-Shirts", "", ()),
        ("Logo Tee", "MULTIPACK T-SHIRTS - M", ()),
    ],
)
def test_tee_category_rejects_kids_and_multipacks(title, product_type, tags):
    assert tee_category(title, product_type, tags) is None


def test_tee_category_keeps_baby_tag_and_nonleading_band_name():
    assert tee_category("Logo Tee", "T-Shirts", ["baby"]) == "t-shirt"
    assert tee_category("Beastie Boys Tee") == "t-shirt"


def test_tee_gender_evidence_and_shop_fallback_order():
    assert tee_gender(
        {"gender_rule": "men"},
        {"title": "Women's Logo Tee"},
        "https://example.test/products/womens-logo-tee",
    ) == "w"
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Logo Tee"},
        "https://example.test/products/logo-tee",
    ) is None
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Logo Tee", "tags": ["Gender:Mens", "Gender:Womens"]},
        "https://example.test/products/logo-tee",
    ) is None
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Logo Tee", "tags": ["womens"]},
        "https://example.test/products/logo-tee",
    ) == "w"
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Logo Tee"},
        "https://example.test/products/logo-tee",
        "m",
    ) == "m"
    assert tee_gender(
        {"gender_rule": "men", "default_gender": "w"},
        {"title": "Logo Tee"},
        "https://example.test/products/logo-tee",
    ) == "m"
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Logo Tee", "tags": ["womens"]},
        "https://example.test/products/logo-tee",
        "m",
    ) == "w"
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Logo Tee", "tags": ["unisex"]},
        "https://example.test/products/logo-tee",
        "m",
    ) == "m"
    assert tee_gender(
        {"gender_rule": "default"},
        {"title": "Unisex Logo Tee"},
        "https://example.test/products/unisex-logo-tee",
        "m",
    ) is None


@pytest.mark.parametrize(
    ("collection_gender", "expected"),
    [("m", "m"), ("w", "w")],
)
def test_tee_gender_uses_rokit_collection_gender_for_mixed_tags(
    collection_gender, expected
):
    product = {
        "title": "Vintage Logo Tee",
        "product_type": "T-Shirts",
        "tags": [
            "ATT|Category two|Mens",
            "ATT|Mens|TRUE",
            "ATT|Womens|FALSE",
            "ATT|Unisex|FALSE",
        ],
    }
    assert tee_gender(
        {"gender_rule": "default"},
        product,
        "https://example.test/products/vintage-logo-tee",
        collection_gender,
    ) == expected


def test_shopify_tee_colour_splitting_and_vendor_brand():
    config = shop(
        "test",
        "Fallback Brand",
        "shopify",
        "example-shop.test",
        max_pages=1,
        brand_from_vendor=True,
        _category_set="tees",
    )
    rows = shopify.scrape(
        config,
        FakeSession(
            [
                fixture("shopify-meta-gbp.json"),
                fixture("shopify-tee-colours.json"),
            ]
        ),
    )

    split_rows = [row for row in rows if row["record"]["raw_json"]["product_id"] == 10]
    assert [row["title"] for row in split_rows] == ["Classic Tee - Blue", "Classic Tee - Red"]
    assert [row["price_gbp"] for row in split_rows] == [24, 30]
    assert [row["brand"] for row in split_rows] == ["Vendor Brand", "Vendor Brand"]
    assert [row["record"]["raw_json"]["colour"] for row in split_rows] == ["Blue", "Red"]
    assert [row["record"]["raw_json"]["variant_id"] for row in split_rows] == [102, 103]
    assert [row["url"].split("?variant=")[-1] for row in split_rows] == ["102", "103"]
    assert split_rows[0]["image_url_original"].endswith("blue-tee.jpg")
    assert split_rows[1]["image_url_original"].endswith("red-tee.jpg")
    assert split_rows[0]["record"]["id"] != split_rows[1]["record"]["id"]
    assert all(row["record"]["raw_json"]["category_set"] == "tees" for row in rows)
    assert all(row["category"] == "t-shirt" for row in rows)
    assert all(row["gender"] is None for row in rows)

    shared_rows = [row for row in rows if row["record"]["raw_json"]["product_id"] == 11]
    assert len(shared_rows) == 1
    assert shared_rows[0]["title"] == "Shared Image Tee"
    assert shared_rows[0]["url"] == "https://example-shop.test/products/shared-image-tee"
    assert shared_rows[0]["brand"] == "Fallback Brand"

    single_colour_rows = [
        row for row in rows if row["record"]["raw_json"]["product_id"] in {12, 13}
    ]
    assert [row["title"] for row in single_colour_rows] == [
        "ESSENTIAL T-SHIRT - Chalk",
        "Charcoal Tee",
    ]


def test_jsonld_uses_tee_pages():
    body = """<script type="application/ld+json">{
      "@type":"Product","name":"Logo Tee",
      "url":"https://example.test/products/logo-tee",
      "image":"https://images.example.test/logo-tee.jpg",
      "offers":{"priceCurrency":"GBP","price":"25",
      "availability":"https://schema.org/InStock"}
    }</script>"""
    config = shop(
        "tees",
        "Example",
        "jsonld",
        "example.test",
        _category_set="tees",
        pages=[{"gender": "m", "url": "https://example.test/apparel"}],
        tee_pages=[{"gender": "w", "url": "https://example.test/tees"}],
    )
    fake = FakeSession([body])

    rows = jsonld.scrape(config, fake)

    assert fake.requests[0]["url"] == "https://example.test/tees"
    assert rows[0]["gender"] == "w"
    assert rows[0]["category"] == "t-shirt"
    assert rows[0]["record"]["raw_json"]["category_set"] == "tees"


def test_shopify_tees_stop_requesting_pages_when_session_is_full():
    fake = FakeSession(
        [
            fixture("shopify-meta-gbp.json"),
            fixture("shopify-tee-colours.json"),
            '{"products": []}',
        ]
    )
    fake.full = False
    fake.on_emit = lambda _rows: setattr(fake, "full", True)
    config = shop(
        "test",
        "Test",
        "shopify",
        "example-shop.test",
        max_pages=4,
        _category_set="tees",
    )

    rows = shopify.scrape(config, fake)

    assert rows
    assert len(fake.requests) == 2


def test_accessory_jsonld_pages_and_tile_acceptance():
    body = """<a href="/product/silk-scarf.html" title="Silk Scarf">
      <span>Silk Scarf £42</span>
      <img src="https://images.example.test/silk-scarf.jpg">
    </a>
    <a href="/product/shampoo.html" title="Shampoo">
      <span>Shampoo £18</span>
      <img src="https://images.example.test/shampoo.jpg">
    </a>"""
    config = shop(
        "whistles",
        "Whistles",
        "jsonld",
        "whistles.com",
        _category_set="accessories",
        pages=[{"gender": "w", "url": "https://www.whistles.com/clothing/"}],
        accessory_pages=[
            {"gender": "w", "url": "https://www.whistles.com/accessories/"},
            {
                "gender": "w",
                "url": "https://www.whistles.com/accessories/?start=60&sz=60",
            },
        ],
    )

    fake = FakeSession([body, body])
    rows = jsonld.scrape(config, fake)
    assert [row["title"] for row in rows] == ["Silk Scarf"]
    assert fake.requests[0]["url"] == "https://www.whistles.com/accessories/"
    assert rows[0]["record"]["raw_json"]["accessory_category"] == "scarf"
    probe = FakeSession([body, body])
    assert jsonld.scrape(config, probe, probe=True)
    assert len(probe.requests) == 2
    assert common.accepts(config, "Gold hoop earrings", title="Gold hoop earrings")
    assert not common.accepts(config, "Shampoo", title="Shampoo")


def test_shopify_reads_three_pages_without_reusing_product_handle():
    pages = json.loads(fixture("shopify-products-pages.json"))
    fake = FakeSession(
        [
            fixture("shopify-meta-gbp.json"),
            *(json.dumps(page) for page in pages),
        ]
    )
    config = shop("test", "Test", "shopify", "example-shop.test", max_pages=20)

    rows = shopify.scrape(config, fake)

    assert len(rows) == 2
    feed_urls = [request["url"] for request in fake.requests[1:]]
    assert all("/products.json?" in url for url in feed_urls)
    assert len(feed_urls) == 3


def test_shopify_rejects_non_gbp_before_products_request():
    fake = FakeSession([fixture("shopify-meta-usd.json")])
    config = shop("test", "Test", "shopify", "example-shop.test")
    assert shopify.scrape(config, fake) == []
    assert config["_status"] == "non_gbp"
    assert len(fake.requests) == 1


def test_shopify_price_country_uses_gbp_product_page_price():
    localized_page = (
        '<script type="application/ld+json">'
        '{"@type":"Product","offers":{"@type":"Offer","priceCurrency":"GBP",'
        '"price":"35.00","availability":"https://schema.org/InStock"}}'
        "</script>"
    )
    fake = FakeSession(
        [
            fixture("shopify-meta-usd.json"),
            fixture("shopify-products.json"),
            localized_page,
        ]
    )
    config = shop(
        "market-shop",
        "Market Shop",
        "shopify",
        "example-shop.test",
        currency="USD",
        price_country="GB",
        max_pages=1,
    )

    rows = shopify.scrape(config, fake)

    assert len(rows) == 1
    assert rows[0]["price_gbp"] == 35
    assert rows[0]["record"]["raw_json"]["price_source"] == "shopify_market_gbp"
    assert fake.requests[-1]["url"].endswith("?country=GB")
    assert config["_price_country_report"] == {
        "country": "GB",
        "currency_required": "GBP",
        "checked": 1,
        "kept": 1,
        "skipped_no_gbp": 0,
        "unverified": 0,
    }


def test_shopify_price_country_skips_non_gbp_product_page_price():
    localized_page = (
        '<script type="application/ld+json">'
        '{"@type":"Product","offers":{"@type":"Offer","priceCurrency":"USD",'
        '"price":"35.00","availability":"https://schema.org/InStock"}}'
        "</script>"
    )
    fake = FakeSession(
        [
            fixture("shopify-meta-usd.json"),
            fixture("shopify-products.json"),
            localized_page,
        ]
    )
    config = shop(
        "market-shop",
        "Market Shop",
        "shopify",
        "example-shop.test",
        currency="USD",
        price_country="GB",
        max_pages=1,
    )

    assert shopify.scrape(config, fake) == []
    assert config["_price_country_report"]["skipped_no_gbp"] == 1
    assert config["_price_country_report"]["kept"] == 0


def test_shopify_localized_gbp_price_reads_open_graph_metadata():
    html = (
        '<meta property="og:price:currency" content="GBP">'
        '<meta property="og:price:amount" content="44.00">'
    )

    assert shopify._localized_gbp_price(html) == 44


def test_tee_shop_configuration_parses_with_expected_market_settings():
    root = Path(__file__).resolve().parents[2]
    shops = tomllib.loads(
        (root / "scraper" / "shops.toml").read_text(encoding="utf-8")
    )["shop"]
    by_key = {entry["key"]: entry for entry in shops}

    assert all(
        by_key[key]["category_sets"] == ["apparel", "tees"]
        for key in (
            "buttergoods",
            "patta",
            "poptradingcompany",
            "storymfg",
            "kith_eu",
            "satisfy",
            "polarskate",
            "stanray",
        )
    )
    assert by_key["dailypaper"]["enabled"] is True
    assert by_key["dailypaper"]["currency"] == "EUR"
    assert by_key["poptradingcompany"]["tee_collections"] == [{"handle": "t-shirts"}]
    assert by_key["hockey"]["collections"] == [{"handle": "hockey"}]
    assert by_key["hockey"]["tee_collections"] == [{"handle": "hockey"}]
    assert {
        entry["key"] for entry in shops if entry.get("price_country") == "GB"
    } == {
        "braindead",
        "fuckingawesome",
        "denimtears",
        "sportyandrich",
        "norseprojects",
        "dime",
        "pleasures",
        "neighborhood",
        "hockey",
    }
    assert by_key["neighborhood"]["currency"] == "JPY"


def test_shopify_gender_clothing_and_variant_availability():
    product = {"product_type": "Womens Dresses", "title": "Cotton Dress"}
    assert shopify.gender_for(product, "sunspel") == "w"
    assert shopify.gender_for({"product_type": "Shirts - W"}, "belstaff") == "w"
    assert common.is_clothing("Cotton dress")
    assert not common.is_clothing("Logo cap")
    assert not common.is_clothing("Kids wool sweater")
    assert not common.is_clothing("Cashmere scarf")
    assert not common.is_clothing("Wool gloves")
    assert not common.is_clothing("Leather loafers")
    assert not common.is_clothing("Gold hoop earrings")
    assert not common.is_clothing("Silver chain necklace")
    assert not common.is_clothing("Luxury watch")
    for title in (
        "Gift wrapping",
        "Gift Box",
        "Gift voucher",
        "Skateboard Hardware",
        "PALACE MOB GRIP",
        "PALACE BOLTS",
        "MTX Unlimited",
        "Repair service",
        "Infant Girls Core Legging",
        "Infant Boys Frame T-Shirt",
        "Girls Icon Cycling Short",
        "Archive - vests",
        "3yrs Junior Hoodie",
    ):
        assert not common.is_clothing(title)
    assert (
        shopify._available_variant(
            {
                "variants": [
                    {"available": False, "price": "10"},
                    {"available": True, "price": "30"},
                    {"available": True, "price": "20"},
                ]
            }
        )[0]
        == 20
    )
    assert (
        shopify._available_variant({"variants": [{"available": False, "price": "10"}]})[1] is None
    )


def test_burberry_api_adapter_parses_available_gbp_product():
    fake = FakeSession([fixture("burberry-nav.html"), fixture("burberry-api.json")])
    config = shop(
        "burberry",
        "Burberry",
        "burberry",
        "burberry.com",
        max_pages=1,
        pages=[
            {
                "gender": "m",
                "path": "/l/mens-clothing/knitwear/",
                "url": "https://uk.burberry.com/l/mens-clothing/knitwear/",
            }
        ],
    )

    rows = burberry.scrape(config, fake)

    assert len(rows) == 1
    assert rows[0]["title"] == "Cotton Knit Jumper"
    assert rows[0]["price_gbp"] == 650
    assert rows[0]["record"]["raw_json"]["product_id"] == "bb-1"
    assert_worker_row(rows[0])


def test_jsonld_category_filters_non_product_and_keeps_gbp():
    fake = FakeSession([fixture("jsonld-listing.html")])
    config = shop(
        "ganni",
        "GANNI",
        "jsonld",
        "ganni.com",
        pages=[{"gender": "w", "url": "https://www.ganni.com/en-gb/clothing/"}],
    )

    rows = jsonld.scrape(config, fake)

    assert len(rows) == 1
    assert rows[0]["title"] == "Jacket in Canvas"
    assert rows[0]["price_gbp"] == 250
    assert_worker_row(rows[0])


def test_jsonld_whistles_and_boss_filter_navigation_and_accessories():
    body = fixture("whistles-boss.html")
    whistles = shop(
        "whistles",
        "Whistles",
        "jsonld",
        "whistles.com",
        pages=[{"gender": "w", "url": "https://www.whistles.com/clothing/knitwear/"}],
    )
    rows = jsonld.scrape(whistles, FakeSession([body]))
    assert [row["title"] for row in rows] == ["Argyle Knit"]

    boss = shop(
        "boss",
        "BOSS",
        "jsonld",
        "hugoboss.com",
        pages=[{"gender": "m", "url": "https://www.hugoboss.com/uk/men-clothing/"}],
    )
    rows = jsonld.scrape(boss, FakeSession([body]))
    assert [row["title"] for row in rows] == ["Wool Shirt"]


def test_nike_wall_tile_parser():
    fake = FakeSession([fixture("nike-wall.html")])
    config = shop(
        "nike",
        "Nike",
        "nike",
        "nike.com",
        pages=[{"gender": "m", "url": "https://www.nike.com/gb/w/mens-tech"}],
    )

    rows = nike.scrape(config, fake, probe=True)

    assert len(rows) == 1
    assert rows[0]["title"] == "Nike Tech Boreas"
    assert rows[0]["price_gbp"] == pytest.approx(159.99)
    assert "/gb/t/" in rows[0]["url"]
    assert_worker_row(rows[0])


def test_nike_rejects_category_jsonld_url():
    body = """<script type="application/ld+json">{
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "Clothing wall",
      "url": "https://www.nike.com/gb/w/clothing-6ymx6",
      "image": "https://images.example.test/nike.jpg",
      "offers": {
        "@type": "Offer",
        "priceCurrency": "GBP",
        "price": "50",
        "availability": "https://schema.org/InStock"
      }
    }</script>"""
    config = shop(
        "nike",
        "Nike",
        "nike",
        "nike.com",
        pages=[{"gender": "m", "url": "https://www.nike.com/gb/w/clothing-6ymx6"}],
    )

    assert nike.scrape(config, FakeSession([body]), probe=True) == []


def test_browser_fetch_omits_irrelevant_wait_selector(monkeypatch):
    from scrapling.fetchers import StealthyFetcher

    options = {}

    def fake_fetch(url, **kwargs):
        options.update(kwargs)
        return SimpleNamespace(status=200, body="ok", url=url)

    monkeypatch.setattr(StealthyFetcher, "fetch", fake_fetch)
    session = ShopSession({"key": "stoneisland"}, check_robots=False)

    session._request("https://example.test/products", browser=True)

    assert "wait_selector" not in options
    assert options["retries"] == 1
    assert options["retry_delay"] == 0


def test_ssense_only_accepts_product_urls():
    assert jsonld._valid_url(
        "", "https://www.ssense.com/en-gb/men/product/burberry/check-shirt/19740321"
    )
    assert not jsonld._valid_url("", "https://www.ssense.com/en-gb/men/designers/burberry/bags")


def test_stone_island_and_tnf_sfcc_parsers():
    body = fixture("sfcc-listings.html")
    stone = shop("stoneisland", "Stone Island", "sfcc", "stoneisland.com")
    stone_rows = sfcc._product_rows(
        stone, body, "https://www.stoneisland.com/en-gb/collection/coats", "m"
    )
    tnf = shop("tnf", "The North Face", "sfcc", "thenorthface.com")
    tnf_rows = sfcc._product_rows(tnf, body, "https://www.thenorthface.com/en-gb/c/men/coats", "m")

    assert stone_rows[0]["title"] == "Down Jacket"
    assert tnf_rows[0]["title"] == "Men's Summit Jacket"
    assert_worker_row(stone_rows[0])
    assert_worker_row(tnf_rows[0])


def test_tiles_adapter_wraps_listing_reader(monkeypatch):
    monkeypatch.setattr(
        tiles,
        "parse_listing",
        lambda *_: [
            {
                "title": "Merino Jumper",
                "price_gbp": 120.0,
                "image_url": "https://images.example.test/jumper.jpg",
                "source_url": "https://www.cos.com/en-gb/product/merino-jumper-123",
                "availability": "InStock",
            }
        ],
    )
    config = shop(
        "cos",
        "COS",
        "tiles",
        "cos.com",
        pages=[{"gender": "m", "url": "https://www.cos.com/en-gb/men/knitwear"}],
    )
    fake = FakeSession(["<html></html>"])

    rows = tiles.scrape(config, fake, probe=True)

    assert rows[0]["title"] == "Merino Jumper"
    assert_worker_row(rows[0])


def test_ralph_lauren_tiles_advance_start_by_thirty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    calls = []

    def parse_page(_body, _url, _domain, _gender):
        calls.append(1)
        if len(calls) == 1:
            return [
                {
                    "title": "Cotton Shirt",
                    "price_gbp": 100,
                    "image_url": "https://images.example.test/rl.jpg",
                    "source_url": "https://www.ralphlauren.co.uk/en/men/shirt/123.html",
                    "availability": "InStock",
                }
            ]
        return []

    monkeypatch.setattr(tiles, "parse_listing", parse_page)
    config = shop(
        "ralphlauren",
        "Ralph Lauren",
        "tiles",
        "ralphlauren.co.uk",
        max_pages=3,
        pages=[{"gender": "m", "url": "https://www.ralphlauren.co.uk/en/men/shirts?start=0&sz=30"}],
    )
    fake = FakeSession(["<html></html>", "<html></html>"])

    rows = tiles.scrape(config, fake)

    assert len(rows) == 1
    assert "start=0" in fake.requests[0]["url"]
    assert "start=30" in fake.requests[1]["url"]


def test_ssense_keeps_product_brand_from_jsonld():
    fake = FakeSession([fixture("ssense-product.html")])
    config = shop(
        "ssense",
        None,
        "ssense",
        "ssense.com",
        pages=[{"gender": "m", "url": "https://www.ssense.com/en-gb/men/designers/stone-island"}],
    )

    rows = ssense.scrape(config, fake)

    assert rows[0]["brand"] == "Stone Island"
    assert_worker_row(rows[0])


def test_ssense_image_template_is_substituted_and_placeholders_are_rejected():
    image = jsonld._image_url("https://img.ssense.com/__IMAGE_PARAMS__/coat.jpg", {"key": "ssense"})
    assert "b_white,c_lpad,g_center,h_960,w_960" in image
    config = shop(domain="example.test")
    page = "https://example.test/category"
    assert (
        make_row(
            config,
            "Coat",
            25,
            "https://images.example.test/__IMAGE_PARAMS__/coat.jpg",
            "https://example.test/p/coat",
            "m",
            page,
        )
        is None
    )
    assert (
        make_row(
            config,
            "Coat",
            25,
            "https://images.example.test/{width}/coat.jpg",
            "https://example.test/p/coat",
            "m",
            page,
        )
        is None
    )


@pytest.mark.parametrize(
    ("status", "body", "classification"),
    [
        (403, "", "blocked"),
        (200, "<html>/cdn-cgi/challenge-platform</html>", "challenge"),
        (
            200,
            '<html><script src="https://captcha-delivery.com/a.js"></script></html>',
            "challenge",
        ),
        (200, "<div id='px-captcha'>Hold</div>", "challenge"),
    ],
)
def test_block_detection_stops_after_one_request(monkeypatch, status, body, classification):
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return SimpleNamespace(status=status, body=body.encode(), url=url)

    monkeypatch.setattr(common.Fetcher, "get", fake_get)
    session = ShopSession(shop(), check_robots=False, delay=0, jitter=0)

    with pytest.raises(common.Blocked) as error:
        session.get("https://example.test/listing")

    assert error.value.status == classification
    if classification == "challenge":
        assert error.value.marker
        assert "matched marker" in str(error.value)
    assert len(calls) == 1
    assert session.count == 1


def test_challenge_detection_ignores_normal_recaptcha_and_json_text():
    body = '<html><article class="product-card">' + ("clothing " * 2500)
    body += '<script src="https://www.google.com/recaptcha/api.js"></script></article></html>'
    assert common.body_has_challenge(body, "jsonld") is None
    assert common.body_has_challenge('{"description":"captcha newsletter form"}', "shopify") is None


def test_robots_disallow_skips_listing_without_request(monkeypatch):
    parser = __import__("urllib.robotparser").robotparser.RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /products"])
    monkeypatch.setitem(common._robots_cache, "example.test", parser)
    session = ShopSession(shop(), delay=0, jitter=0)

    assert session.get("https://example.test/products/coat") is None
    assert session.skipped == ["https://example.test/products/coat"]
    assert session.count == 0


def test_validation_rejects_bad_price_image_domain_stock_and_footwear():
    config = shop(domain="example.test")
    page = "https://example.test/category/shirts"
    good = ("Coat", 20, "https://images.example.test/coat.jpg", "https://example.test/p/coat")
    assert make_row(config, good[0], 0, good[2], good[3], "m", page) is None
    assert (
        make_row(
            config, good[0], good[1], "http://images.example.test/coat.jpg", good[3], "m", page
        )
        is None
    )
    assert (
        make_row(config, good[0], good[1], good[2], "https://outside.test/p/coat", "m", page)
        is None
    )
    assert make_row(config, "Trainers", good[1], good[2], good[3], "m", page) is None
    assert (
        make_row(
            config, good[0], good[1], good[2], good[3], "m", page, {"availability": "OutOfStock"}
        )
        is None
    )
    assert make_row(config, *good, "m", page) is not None


def test_url_dedupe_and_batch_splitting():
    rows = [
        make_row(
            shop(domain="example.test"),
            f"Coat {index}",
            20,
            "https://images.example.test/i.jpg",
            f"https://example.test/p/{index}",
            "m",
            "https://example.test/category",
        )
        for index in range(3)
    ]
    assert len(unique_rows([rows[0], rows[0], rows[1]])) == 2
    assert [len(batch) for batch in split_batches(rows, 2)] == [2, 1]


def test_all_failed_sampled_images_mark_shop_broken():
    assert _images_broken([{"status": 404}, {"status": "TimeoutException"}])
    assert not _images_broken([{"status": 404}, {"status": 200}])
    assert not _images_broken([])


def test_sfcc_pagination_uses_site_next_link():
    next_url = sfcc.pagination_next(
        '<a href="?start=17">Load more</a>',
        "https://www.stoneisland.com/en-gb/collection/coats",
    )
    assert next_url == "https://www.stoneisland.com/en-gb/collection/coats?start=17"


def test_upload_cli_path_uses_mocked_store(monkeypatch, tmp_path):
    import scrape

    uploaded = []
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.invalid")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setattr(scrape, "ROOT", tmp_path)
    monkeypatch.setattr(scrape, "_ensure_bucket", lambda *_: None)
    monkeypatch.setattr(
        scrape, "upload_rows", lambda _client, _connection, rows, *_: uploaded.extend(rows)
    )
    monkeypatch.setattr(
        scrape.httpx, "Client", lambda **_kwargs: SimpleNamespace(close=lambda: None)
    )

    class FakeConnection:
        def close(self):
            pass

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda _dsn: FakeConnection())
    row = make_row(
        shop(domain="example.test"),
        "Coat",
        20,
        "https://images.example.test/coat.jpg",
        "https://example.test/p/coat",
        "m",
        "https://example.test/category",
    )
    monkeypatch.setitem(
        scrape.REGISTRY,
        "test",
        lambda _shop, session: session.emit([row]) or [row],
    )
    args = SimpleNamespace(
        max_pages=0,
        max_requests=60,
        delay=0,
        jitter=0,
        time_budget=1,
        workers=1,
        batch_size=10,
        check_images=0,
        upload=True,
    )

    scrape._run([shop(key="upload", adapter="test", domain="example.test")], args)

    assert len(uploaded) == 1
    assert uploaded[0]["url"] == row["url"]


def test_store_upload_inserts_on_conflict_with_mocked_image(monkeypatch):
    executed = []
    monkeypatch.setattr(store, "_upload_image", lambda *_: "test/asset.jpg")

    class Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query, params):
            executed.append((query, params))

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

    row = make_row(
        shop(domain="example.test"),
        "Coat",
        20,
        "https://images.example.test/coat.jpg",
        "https://example.test/p/coat",
        "m",
        "https://example.test/category",
    )
    store.upload_rows(
        None, Connection(), [row], shop(domain="example.test"), "https://supabase.invalid", "mocked"
    )

    assert "ON CONFLICT (url) DO NOTHING" in executed[0][0]
    assert row["image_path"] == "test/asset.jpg"


def test_legacy_worker_upload_uses_shop_config(monkeypatch, tmp_path):
    import psycopg
    import worker

    uploads = []
    record = {
        "source_url": "https://example.test/p/coat",
        "title": "Coat",
        "price_gbp": 20,
        "image_url": "https://images.example.test/coat.jpg",
    }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(
        worker, "CATEGORY_PAGES", {"example.test": [("m", "https://example.test/coats")]}
    )
    monkeypatch.setattr(
        worker,
        "_fetch",
        lambda *_args, **_kwargs: ("html", "https://example.test/coats"),
    )
    monkeypatch.setattr(worker, "parse_listing", lambda *_args: [record])
    monkeypatch.setattr(worker, "_ensure_bucket", lambda *_args: None)
    monkeypatch.setattr(
        worker,
        "upload_row",
        lambda _client, _connection, _row, shop, *_args: uploads.append(shop) or True,
    )
    monkeypatch.setattr(worker.httpx, "Client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(
        psycopg, "connect", lambda _dsn: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.invalid")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")

    worker.run(SimpleNamespace(time_budget=1, shops=None, max_pages=0, dry_run=False))

    assert uploads == [{"domain": "example.test"}]


def test_accessory_shop_selection_and_sharding():
    shops = [
        shop("shopify", adapter="shopify", enabled=True),
        shop(
            "jsonld-accessories",
            adapter="jsonld",
            enabled=True,
            category_sets=["accessories"],
            accessory_pages=[{"url": "https://example.test/accessories"}],
        ),
        shop(
            "jsonld-apparel",
            adapter="jsonld",
            enabled=True,
            pages=[{"url": "https://example.test/clothing"}],
        ),
        shop(
            "accessory-only",
            adapter="shopify",
            enabled=True,
            category_sets=["accessories"],
        ),
    ]
    accessories_args = SimpleNamespace(
        shops=None,
        group=None,
        category_set="accessories",
        uk=False,
        all_enabled=False,
    )
    apparel_args = SimpleNamespace(
        shops=None,
        group=None,
        category_set="apparel",
        uk=False,
        all_enabled=False,
    )

    assert [
        item["key"] for item in _shop_filter(shops, accessories_args)
    ] == ["shopify", "jsonld-accessories", "accessory-only"]
    assert [
        item["key"] for item in _shop_filter(shops, apparel_args)
    ] == ["shopify", "jsonld-apparel"]
    assert [item["key"] for item in _shard_shops(shops, "1/2")] == [
        "jsonld-accessories",
        "accessory-only",
    ]
    with pytest.raises(ValueError, match="0 <= K < N"):
        _shard_shops(shops, "2/2")


def test_apparel_and_tee_shop_selection():
    shops = [
        shop("legacy", adapter="shopify", enabled=True),
        shop(
            "apparel-and-tees",
            adapter="shopify",
            enabled=True,
            category_sets=["apparel", "tees"],
        ),
        shop("tees-only", adapter="shopify", enabled=True, category_sets=["tees"]),
        shop("accessories-only", adapter="shopify", enabled=True, category_sets=["accessories"]),
    ]
    args = SimpleNamespace(
        shops=None,
        group=None,
        category_set="tees",
        uk=False,
        all_enabled=False,
    )
    tee_keys = [item["key"] for item in _shop_filter(shops, args)]
    args.category_set = "apparel"
    apparel_keys = [item["key"] for item in _shop_filter(shops, args)]

    assert tee_keys == ["apparel-and-tees", "tees-only"]
    assert apparel_keys == ["legacy", "apparel-and-tees"]


def test_run_caps_accepted_rows_per_shop(monkeypatch, tmp_path):
    monkeypatch.setattr(scrape, "ROOT", tmp_path)
    config = shop(
        "capped",
        adapter="test",
        domain="example.test",
        _category_set="tees",
    )
    rows = [
        make_row(
            config,
            f"Logo Tee {index}",
            20,
            "https://images.example.test/tee.jpg",
            f"https://example.test/products/tee-{index}",
            None,
            "https://example.test/tees",
        )
        for index in range(4)
    ]

    def emit_rows(_shop, session):
        session.emit(rows)
        assert session.full

    monkeypatch.setitem(scrape.REGISTRY, "test", emit_rows)
    args = SimpleNamespace(
        category_set="tees",
        shard=None,
        resume=None,
        known=None,
        time_budget=1,
        upload=False,
        workers=1,
        max_pages=0,
        max_requests=10,
        delay=0,
        jitter=0,
        max_rows_per_shop=2,
        check_images=0,
        batch_size=10,
    )

    summary = scrape._run([config], args)

    assert summary["total_rows"] == 2
    assert summary["shops"][0]["rows"] == 2
    assert len((tmp_path / "out" / summary["run_id"] / "all.jsonl").read_text().splitlines()) == 2


def test_resume_loads_url_and_record_id_dedupes_and_skips_done_shops(tmp_path, monkeypatch):
    resume_dir = tmp_path / "resume"
    resume_dir.mkdir()
    existing_url_row = make_row(
        shop(domain="example.test", _category_set="accessories"),
        "Tote Bag",
        50,
        "https://images.example.test/tote.jpg",
        "https://example.test/products/known-url",
        None,
        "https://example.test/accessories",
    )
    existing_url_row["record"]["id"] = "url-record"
    existing_id_row = make_row(
        shop(domain="example.test", _category_set="accessories"),
        "Leather Belt",
        40,
        "https://images.example.test/belt.jpg",
        "https://example.test/products/known-id",
        None,
        "https://example.test/accessories",
    )
    existing_id_row["record"]["id"] = "known-record-id"
    (resume_dir / "all.jsonl").write_text(
        json.dumps(existing_url_row) + "\n", encoding="utf-8"
    )
    (resume_dir / "previous-shop.jsonl").write_text(
        json.dumps(existing_id_row) + "\n", encoding="utf-8"
    )
    (resume_dir / "progress.json").write_text(
        json.dumps({"category_set": "accessories", "done": ["done-shop"]}),
        encoding="utf-8",
    )

    duplicate_url = make_row(
        shop(domain="example.test", _category_set="accessories"),
        "Different Tote Bag",
        60,
        "https://images.example.test/other-tote.jpg",
        existing_url_row["url"],
        None,
        "https://example.test/accessories",
    )
    duplicate_url["record"]["id"] = "different-record"
    duplicate_id = make_row(
        shop(domain="example.test", _category_set="accessories"),
        "Another Belt",
        45,
        "https://images.example.test/another-belt.jpg",
        "https://example.test/products/another-belt",
        None,
        "https://example.test/accessories",
    )
    duplicate_id["record"]["id"] = "known-record-id"
    fresh = make_row(
        shop(domain="example.test", _category_set="accessories"),
        "Wool Scarf",
        35,
        "https://images.example.test/scarf.jpg",
        "https://example.test/products/fresh-scarf",
        None,
        "https://example.test/accessories",
    )
    fresh["record"]["id"] = "fresh-record-id"
    emitted = [duplicate_url, duplicate_id, fresh]
    called = []

    def fake_adapter(config, session):
        called.append(config["key"])
        assert config["_category_set"] == "accessories"
        session.emit(emitted)

    monkeypatch.setitem(scrape.REGISTRY, "test", fake_adapter)
    args = SimpleNamespace(
        category_set="accessories",
        shard=None,
        resume=str(resume_dir),
        known=None,
        time_budget=1,
        upload=False,
        workers=1,
        max_pages=0,
        max_requests=10,
        delay=0,
        jitter=0,
        check_images=0,
        batch_size=10,
    )

    summary = scrape._run(
        [
            shop(key="done-shop", adapter="test", domain="example.test"),
            shop(key="target", adapter="test", domain="example.test"),
        ],
        args,
    )

    assert called == ["target"]
    assert summary["total_rows"] == 2
    final_rows = [json.loads(line) for line in (resume_dir / "all.jsonl").read_text().splitlines()]
    assert {row["url"] for row in final_rows} == {
        existing_url_row["url"],
        fresh["url"],
    }
    assert len({row["record"]["id"] for row in final_rows}) == len(final_rows)
    progress = json.loads((resume_dir / "progress.json").read_text())
    assert set(progress["done"]) == {"done-shop", "target"}


def test_known_jsonl_preseeds_record_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "ROOT", tmp_path)
    known_path = tmp_path / "known.jsonl"
    known = make_row(
        shop(domain="example.test"),
        "Cotton Coat",
        30,
        "https://images.example.test/coat.jpg",
        "https://example.test/products/known",
        "m",
        "https://example.test/category",
    )
    known["record"]["id"] = "known-record-id"
    known_path.write_text(json.dumps(known) + "\n", encoding="utf-8")
    incoming = dict(known)
    incoming["url"] = "https://example.test/products/different"
    incoming["record"] = dict(known["record"], id="known-record-id")

    monkeypatch.setitem(
        scrape.REGISTRY,
        "test",
        lambda _shop, session: session.emit([incoming]),
    )
    args = SimpleNamespace(
        category_set="apparel",
        shard=None,
        resume=None,
        known=[str(known_path)],
        time_budget=1,
        upload=False,
        workers=1,
        max_pages=0,
        max_requests=10,
        delay=0,
        jitter=0,
        check_images=0,
        batch_size=10,
    )

    summary = scrape._run(
        [shop(key="known-test", adapter="test", domain="example.test")],
        args,
    )

    assert summary["total_rows"] == 0


def test_markdown_handles_unisex_accessories_with_no_gender():
    summary = {
        "run_id": "test",
        "total_rows": 2,
        "shops": [
            {
                "key": "accessories",
                "status": "ok",
                "requests": [],
                "rows": 2,
                "gender_split": {None: 1, "w": 1},
                "price_min": 10,
                "price_max": 20,
                "sample": {},
                "reason": "",
            }
        ],
    }
    summary = json.loads(json.dumps(summary))

    markdown = scrape._markdown(summary)

    assert "unknown 1, w 1" in markdown


def test_resume_refuses_a_different_category_set(tmp_path):
    resume_dir = tmp_path / "resume"
    resume_dir.mkdir()
    (resume_dir / "progress.json").write_text(
        json.dumps({"category_set": "apparel", "done": []}),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        category_set="accessories",
        shard=None,
        resume=str(resume_dir),
        known=None,
    )

    with pytest.raises(ValueError, match="cannot resume"):
        scrape._run([], args)
