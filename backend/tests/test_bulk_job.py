import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from app import supabase_import

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("bulk_job", ROOT / "scraper" / "bulk_job.py")
bulk_job = importlib.util.module_from_spec(SPEC)
sys.modules["bulk_job"] = bulk_job
SPEC.loader.exec_module(bulk_job)


def test_shopify_keeps_each_available_color_and_skips_unavailable():
    products = [{
        "id": 1,
        "handle": "shirt",
        "title": "Oxford Shirt",
        "vendor": "Example",
        "options": [{"name": "Colour", "position": 1, "values": ["Blue", "Red"]}],
        "variants": [
            {"id": 11, "available": True, "price": "45.00", "option1": "Blue"},
            {"id": 12, "available": False, "price": "45.00", "option1": "Red"},
            {"id": 13, "available": True, "price": "45.00", "option1": "Red"},
        ],
        "images": [{"src": "https://cdn.example.test/shirt.jpg"}],
    }]

    rows, skipped = bulk_job._shopify_rows("shop.example", products, "GBP")

    assert skipped == {}
    assert [row["record"]["title"] for row in rows] == [
        "Oxford Shirt - Blue", "Oxford Shirt - Red",
    ]
    assert [row["url"] for row in rows] == [
        "https://shop.example/products/shirt?variant=11",
        "https://shop.example/products/shirt?variant=13",
    ]


def test_shopify_drops_product_without_available_variants():
    rows, skipped = bulk_job._shopify_rows("shop.example", [{
        "handle": "sold", "title": "Sold Shirt",
        "variants": [{"available": False, "price": "10"}],
        "images": [{"src": "https://cdn.example.test/sold.jpg"}],
    }], "GBP")
    assert rows == []
    assert skipped == {"out_of_stock": 1}


def test_jsonld_requires_instock_gbp_clothing_product():
    product = {
        "@type": "Product",
        "name": "Men's Denim Jacket",
        "category": "Clothing",
        "brand": {"name": "Example"},
        "image": ["https://cdn.example.test/jacket.jpg"],
        "offers": {
            "@type": "Offer", "price": "75.00", "priceCurrency": "GBP",
            "availability": "https://schema.org/InStock",
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'

    record, reason = bulk_job.parse_product_page(
        html, "https://shop.example/jacket", "shop.example"
    )

    assert record is not None
    assert reason is None
    assert record["price_gbp"] == 75.0
    assert record["availability"] == "https://schema.org/InStock"
    product["offers"]["availability"] = "https://schema.org/OutOfStock"
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'
    record, reason = bulk_job.parse_product_page(
        html, "https://shop.example/jacket", "shop.example"
    )
    assert record is None
    assert reason == "out_of_stock"


def test_jsonld_rejects_beauty_products_even_if_they_have_a_clothing_category():
    product = {
        "@type": "Product",
        "name": "Lola Long-Lasting Matte Lipstick",
        "category": "Accessories",
        "image": ["https://cdn.example.test/lipstick.jpg"],
        "offers": {
            "@type": "Offer", "price": "17.58", "priceCurrency": "GBP",
            "availability": "https://schema.org/InStock",
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'

    record, reason = bulk_job.parse_product_page(
        html, "https://shop.example/lipstick", "shop.example"
    )

    assert record is None
    assert reason == "non_apparel"


def test_jsonld_rejects_home_products_even_if_they_have_a_clothing_category():
    product = {
        "@type": "Product",
        "name": "Set Of Four White Marble Coasters With Brass Holder",
        "category": "Accessories",
        "image": ["https://cdn.example.test/coasters.jpg"],
        "offers": {
            "@type": "Offer", "price": "27.99", "priceCurrency": "GBP",
            "availability": "https://schema.org/InStock",
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'

    record, reason = bulk_job.parse_product_page(
        html, "https://shop.example/coasters", "shop.example"
    )

    assert record is None
    assert reason == "non_apparel"


def test_sitemap_discovery_prefers_uk_product_sitemaps():
    class Response:
        status_code = 200

        def __init__(self, body):
            self.text = body
            self.content = body.encode()

    class Client:
        def get(self, url, interval):
            if url.endswith("/robots.txt"):
                return Response(
                    "Sitemap: https://www.hugoboss.com/at/sitemap_index.xml\n"
                    "Sitemap: https://www.hugoboss.com/uk/sitemap_index.xml"
                )
            if url.endswith("/uk/sitemap_index.xml"):
                return Response(
                    "<sitemapindex>"
                    "<sitemap><loc>https://www.hugoboss.com/uk/sitemap_0.xml</loc></sitemap>"
                    "<sitemap><loc>https://www.hugoboss.com/uk/"
                    "sitemap-refinedCategories_UK.xml</loc></sitemap>"
                    "</sitemapindex>"
                )
            if url.endswith("/uk/sitemap_0.xml"):
                return Response(
                    "<urlset><url><loc>https://www.hugoboss.com/uk/"
                    "black-sunglasses-with-two-tone-temples/"
                    "hbeu58332843.html</loc></url></urlset>"
                )
            return Response("<urlset></urlset>")

    urls = bulk_job.sitemap_product_urls(Client(), "https://www.hugoboss.com")
    assert urls == [
        "https://www.hugoboss.com/uk/black-sunglasses-with-two-tone-temples/"
        "hbeu58332843.html"
    ]


def test_vinted_unavailable_statuses_are_rejected():
    assert bulk_job._vinted_unavailable({"is_reserved": True})
    assert bulk_job._vinted_unavailable({"status": {"title": "Sold"}})
    assert not bulk_job._vinted_unavailable({"status": {"title": "Active"}})


def test_vinted_pacing_uses_elapsed_monotonic_time(monkeypatch):
    sleeps = []
    monkeypatch.setattr(bulk_job.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(bulk_job.time, "sleep", sleeps.append)

    bulk_job._wait_for_vinted_request(99.6)

    assert len(sleeps) == 1
    assert abs(sleeps[0] - 0.6) < 1e-8


def test_append_record_is_durable_and_resume_dedupes(tmp_path):
    state_path = tmp_path / "state.json"
    state = {"done": {}, "counts": {}}
    pending = {}
    seen = set()
    row = {"url": "https://shop.example/item", "source": "shop.example"}

    assert bulk_job.append_record(tmp_path, "shop.example", row, seen, pending, state, state_path)
    assert not bulk_job.append_record(
        tmp_path, "shop.example", row, seen, pending, state, state_path
    )
    saved = json.loads(state_path.read_text())
    assert saved["counts"]["shop.example"]["pending"] == 1
    resumed_seen, resumed_pending = bulk_job.load_seen_and_pending(tmp_path, set())
    assert row["url"] in resumed_seen
    assert len(resumed_pending["shop.example"]) == 1


def test_counter_migration_accounts_for_skips_and_pending(tmp_path):
    state = {
        "counts": {
            "boohoo.com": {
                "scraped": 11,
                "skipped_out_of_stock": 9,
                "imported": 9,
                "tag_errors": 1,
            }
        }
    }
    pending = {"boohoo.com": [{"url": "failed"}, {"url": "waiting"}]}

    bulk_job.reconcile_counts(state, pending)

    counts = state["counts"]["boohoo.com"]
    assert counts["scraped"] == 20
    assert counts["skipped_by_reason"] == {"out_of_stock": 9}
    assert counts["pending"] == 2
    assert counts["scraped"] == (
        counts["imported"] + sum(counts["skipped_by_reason"].values()) + counts["pending"]
    )


def test_smoke_shopify_429_does_not_backoff(monkeypatch):
    class Response:
        status_code = 429

    class Client:
        calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = Client()
    monkeypatch.setattr(
        bulk_job.time, "sleep",
        lambda *_args: (_ for _ in ()).throw(AssertionError("smoke must not back off")),
    )

    response = bulk_job._shopify_get(
        client, "https://shop.example/products.json", 600, smoke=True
    )

    assert response.status_code == 429
    assert client.calls == 1


def test_source_families_run_concurrently():
    barrier = threading.Barrier(3)
    completed = []

    def family(source):
        barrier.wait(timeout=3)
        completed.append(source)

    errors = bulk_job.run_source_families({
        "vinted": lambda: family("vinted"),
        "sitemaps": lambda: family("sitemaps"),
        "shopify": lambda: family("shopify"),
    })

    assert errors == {}
    assert sorted(completed) == ["shopify", "sitemaps", "vinted"]


class _Cursor:
    def __init__(self, inserted):
        self.inserted = inserted

    def fetchone(self):
        return (self.inserted,) if self.inserted else None


class _Connection:
    def __init__(self):
        self.sql = []
        self.commits = 0

    def execute(self, sql, params):
        self.sql.append(sql)
        return _Cursor(params[2])

    def commit(self):
        self.commits += 1


def _import_row():
    record = {
        "id": "item-1", "source": "shop.example",
        "source_url": "https://shop.example/item", "title": "Jacket",
        "brand": "Example", "price_gbp": 30, "image_url": "https://img.example/jacket.jpg",
        "availability": "https://schema.org/InStock", "raw_json": {},
    }
    return {
        "url": record["source_url"], "source": "shop.example", "title": "Jacket",
        "brand": "Example", "price_gbp": 30, "currency": "GBP",
        "image_url_original": record["image_url"], "record": record,
    }


def test_insert_only_import_tags_before_a_single_items_insert(tmp_path, monkeypatch):
    path = tmp_path / "chunk.jsonl"
    path.write_text(json.dumps(_import_row()) + "\n")
    connection = _Connection()
    tag_calls = []
    monkeypatch.setattr(supabase_import, "_download_original_image", lambda _url: b"bytes")
    monkeypatch.setattr(supabase_import, "_cache_image", lambda *_args: "/tmp/image.jpg")
    monkeypatch.setattr(supabase_import.images, "storage_enabled", lambda: False)

    def tag_item(item, *, log_attempts):
        tag_calls.append((item["source_url"], log_attempts))
        return (
            SimpleNamespace(model_dump=lambda mode: {"category": "clothing"}),
            SimpleNamespace(request_id="req-1"),
        )

    monkeypatch.setattr(supabase_import, "tag_item", tag_item)
    result = supabase_import.run_insert_only_file_import(
        [path], connection, set(),
    )

    assert result["inserted"] == result["tagged"] == 1
    assert tag_calls == [("https://shop.example/item", False)]
    assert len(connection.sql) == connection.commits == 1
    assert "INSERT INTO quenya.items" in connection.sql[0]
    assert "UPDATE " not in connection.sql[0]


def test_insert_only_import_never_inserts_out_of_stock(tmp_path, monkeypatch):
    row = _import_row()
    row["record"]["availability"] = "https://schema.org/OutOfStock"
    path = tmp_path / "chunk.jsonl"
    path.write_text(json.dumps(row) + "\n")
    connection = _Connection()
    result = supabase_import.run_insert_only_file_import([path], connection, set())

    assert result["skipped_out_of_stock"] == 1
    assert connection.sql == []


def test_insert_only_import_pauses_after_consecutive_tag_errors(tmp_path, monkeypatch):
    path = tmp_path / "chunk.jsonl"
    rows = []
    for index in range(2):
        row = _import_row()
        row["url"] = f"https://shop.example/item-{index}"
        row["record"]["source_url"] = row["url"]
        row["record"]["id"] = f"item-{index}"
        rows.append(row)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(supabase_import, "_download_original_image", lambda _url: b"bytes")
    monkeypatch.setattr(supabase_import, "_cache_image", lambda *_args: "/tmp/image.jpg")
    monkeypatch.setattr(supabase_import.images, "storage_enabled", lambda: False)
    def fail_tag(*_args, **_kwargs):
        raise RuntimeError("Mubit unavailable")

    monkeypatch.setattr(supabase_import, "tag_item", fail_tag)
    connection = _Connection()

    result = supabase_import.run_insert_only_file_import(
        [path], connection, set(), max_tag_failure_streak=2,
    )

    assert result["imports_paused"]
    assert result["tag_failed"] == 2
    assert connection.sql == []
