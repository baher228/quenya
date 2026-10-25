from pathlib import Path
from types import SimpleNamespace

import pytest
import store
from adapters import burberry, common, jsonld, nike, sfcc, shopify, ssense, tiles
from adapters.common import ShopSession, make_row, unique_rows
from scrape import split_batches

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


def test_shopify_rejects_non_gbp_before_products_request():
    fake = FakeSession([fixture("shopify-meta-usd.json")])
    config = shop("test", "Test", "shopify", "example-shop.test")
    assert shopify.scrape(config, fake) == []
    assert config["_status"] == "non_gbp"
    assert len(fake.requests) == 1


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


@pytest.mark.parametrize(
    ("status", "body", "classification"),
    [(403, "", "blocked"), (200, "cf-chl captcha", "challenge")],
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
    assert len(calls) == 1
    assert session.count == 1


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
