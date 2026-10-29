import hashlib
import json

import pytest

from app import ingest
from app.sources import lv

PRODUCT_URL = (
    "https://uk.louisvuitton.com/eng-gb/products/"
    "belted-carrot-trousers-nvprod7480069v/1AJYWM"
)
PRODUCT = {
    "@type": "Product",
    "name": "Belted Carrot Trousers",
    "image": [
        "https://uk.louisvuitton.com/images/trousers-main.jpg",
        "https://uk.louisvuitton.com/images/trousers-alt.jpg",
    ],
    "description": "Tailored trousers in wool.",
    "offers": [
        {
            "@type": "Offer",
            "url": PRODUCT_URL,
            "priceCurrency": "GBP",
            "price": "1140",
            "availability": "https://schema.org/InStock",
        }
    ],
}
PRODUCT_HTML = (
    "<html><head>"
    '<script type="application/ld+json">'
    f"{json.dumps(PRODUCT)}"
    "</script>"
    '<script type="application/ld+json">'
    '{"@type":"BreadcrumbList","itemListElement":['
    '{"@type":"ListItem","position":1,"name":"Men"},'
    '{"@type":"ListItem","position":2,"name":"Ready-to-wear"}]}'
    "</script></head><body></body></html>"
)


def _url(slug: str, sku: str = "SKU123") -> str:
    return f"https://uk.louisvuitton.com/eng-gb/products/{slug}/{sku}"


def _sitemap(*urls: str) -> str:
    return (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>"
    )


class _Response:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")


class _Session:
    def __init__(self, sitemap: _Response, page_responses: list):
        self.sitemap = sitemap
        self.page_responses = list(page_responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == lv.SITEMAP_URL:
            return self.sitemap
        response = self.page_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _OrderedRng:
    def shuffle(self, values):
        return None

    def uniform(self, low, high):
        return (low + high) / 2


def test_clothing_urls_keeps_apparel_and_excludes_accessories_and_other_hosts():
    apparel = _url("belted-carrot-trousers-nvprod7480069v", "1AJYWM")
    shirt = _url("monogram-silk-t-shirt-nvprod123v", "1AA001")
    urls = lv.clothing_urls(
        _sitemap(
            apparel,
            shirt,
            _url("neverfull-mm-nvprod123v"),
            _url("fastlane-sneaker-nvprod123v"),
            _url("ombre-nomade-perfume-nvprod123v"),
            _url("monogram-bag-charm-shirt-nvprod123v"),
            "https://example.com/eng-gb/products/wool-jacket/123",
        )
    )
    assert urls == [apparel, shirt]


def test_parse_product_extracts_the_first_offer_image_and_breadcrumb_gender():
    record = lv.parse_product(
        PRODUCT_HTML,
        f"{PRODUCT_URL}?size=large#product",
    )

    assert record is not None
    assert record["id"] == "lv:1AJYWM"
    assert record["source"] == "louisvuitton"
    assert record["source_url"] == PRODUCT_URL
    assert record["title"] == "Belted Carrot Trousers"
    assert record["brand"] == "Louis Vuitton"
    assert record["price_gbp"] == 1140.0
    assert record["image_url"] == PRODUCT["image"][0]
    assert record["gender_hint"] == "m"
    assert record["condition_hint"] == "new"
    assert record["availability"] == "https://schema.org/InStock"
    assert record["query"] == "lv-sitemap"
    assert record["raw_json"] == PRODUCT


@pytest.mark.parametrize(
    "product",
    [
        {**PRODUCT, "offers": {**PRODUCT["offers"][0], "priceCurrency": "EUR"}},
        {**PRODUCT, "offers": {"priceCurrency": "GBP"}},
    ],
)
def test_parse_product_rejects_non_gbp_or_missing_price(product):
    html = (
        '<script type="application/ld+json">'
        f"{json.dumps(product)}"
        "</script>"
    )
    assert lv.parse_product(html, PRODUCT_URL) is None


def test_parse_product_rejects_html_without_product_jsonld():
    assert lv.parse_product(
        '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>',
        PRODUCT_URL,
    ) is None


def test_parse_product_uses_sha1_when_the_url_has_no_sku_tail():
    product_url = (
        "https://uk.louisvuitton.com/eng-gb/products/"
        "belted-carrot-trousers-nvprod7480069v"
    )
    record = lv.parse_product(PRODUCT_HTML, product_url)
    assert record is not None
    assert record["id"] == f"lv:{hashlib.sha1(product_url.encode()).hexdigest()}"


def test_trickle_stops_after_three_consecutive_blocks():
    urls = [_url(f"wool-shirt-nvprod{index}v", f"SKU{index}") for index in range(5)]
    session = _Session(
        _Response(200, _sitemap(*urls)),
        [
            _Response(403, "blocked"),
            _Response(200, "x" * 2_500),
            TimeoutError("request timed out"),
        ],
    )
    delays = []

    records, report = lv.trickle(
        5,
        session=session,
        sleep=delays.append,
        rng=_OrderedRng(),
        existing_urls=set(),
        delay_range=(20, 60),
    )

    assert records == []
    assert report["attempted"] == 3
    assert report["blocked"] == 3
    assert report["stopped_after_blocks"] is True
    assert len([call for call in session.calls if call[0] != lv.SITEMAP_URL]) == 3
    assert len(delays) == 2
    assert all(20 <= delay <= 60 for delay in delays)


def test_trickle_skips_existing_urls_respects_limit_and_uses_injected_sleep(monkeypatch):
    urls = [_url(f"wool-shirt-nvprod{index}v", f"SKU{index}") for index in range(5)]
    session = _Session(
        _Response(200, _sitemap(*urls)),
        [_Response(200, PRODUCT_HTML), _Response(200, PRODUCT_HTML)],
    )
    delays = []
    monkeypatch.setattr(
        lv.db,
        "source_urls",
        lambda: pytest.fail("explicit existing_urls should avoid the database"),
    )

    records, report = lv.trickle(
        2,
        session=session,
        sleep=delays.append,
        rng=_OrderedRng(),
        existing_urls=set(urls[:2]),
        delay_range=(20, 60),
    )

    requested = [url for url, _kwargs in session.calls if url != lv.SITEMAP_URL]
    assert len(records) == 2
    assert requested == urls[2:4]
    assert report["clothing_urls"] == 5
    assert report["skipped_existing"] == 2
    assert report["attempted"] == 2
    assert report["ok"] == 2
    assert report["blocked"] == 0
    assert len(delays) == 1
    assert all(20 <= delay <= 60 for delay in delays)


def test_trickle_counts_parse_failures_without_treating_them_as_blocks():
    urls = [_url(f"wool-shirt-nvprod{index}v", f"SKU{index}") for index in range(2)]
    invalid_product = {**PRODUCT, "offers": {**PRODUCT["offers"][0], "priceCurrency": "EUR"}}
    invalid_html = (
        '<script type="application/ld+json">'
        f"{json.dumps(invalid_product)}"
        "</script>"
        + ("x" * 5_000)
    )
    session = _Session(
        _Response(200, _sitemap(*urls)),
        [_Response(200, invalid_html), _Response(200, PRODUCT_HTML)],
    )

    records, report = lv.trickle(
        2,
        session=session,
        sleep=lambda _delay: None,
        rng=_OrderedRng(),
        existing_urls=set(),
    )

    assert len(records) == 1
    assert report["attempted"] == 2
    assert report["parse_failed"] == 1
    assert report["ok"] == 1
    assert report["blocked"] == 0


def test_trickle_reports_sitemap_failures_without_raising():
    session = _Session(_Response(403, "blocked"), [])
    records, report = lv.trickle(
        2,
        session=session,
        existing_urls=set(),
    )
    assert records == []
    assert report["error"] == "sitemap_http_403"
    assert report["attempted"] == 0


def test_ingest_lv_uses_trickle_and_skips_seed_queries(monkeypatch):
    existing = {"https://uk.louisvuitton.com/existing"}
    record = {
        "id": "lv:1AJYWM",
        "source": "louisvuitton",
        "source_url": PRODUCT_URL,
        "title": "Belted Carrot Trousers",
    }
    trickle_report = {"attempted": 1, "ok": 1}
    calls = {}
    monkeypatch.setattr(ingest.db, "init_db", lambda: None)
    monkeypatch.setattr(ingest.db, "source_urls", lambda: existing)
    monkeypatch.setattr(
        ingest,
        "read_seeds",
        lambda: pytest.fail("LV ingestion must not read seed queries"),
    )

    def trickle(limit, *, existing_urls):
        calls["trickle"] = (limit, existing_urls)
        return [record], trickle_report

    monkeypatch.setattr(ingest.lv, "trickle", trickle)
    monkeypatch.setattr(ingest.db, "insert_items", lambda records: len(records))
    monkeypatch.setattr(
        ingest.tag,
        "tag_untagged",
        lambda *args, **kwargs: calls.setdefault("tag", (args, kwargs)) or {},
    )
    monkeypatch.setattr(
        ingest.embed,
        "backfill",
        lambda batch_size, *, limit: calls.setdefault("embed", (batch_size, limit)),
    )

    summary = ingest.ingest(
        "lv",
        10,
        limit=7,
        run_tag=True,
        run_embed=True,
        embed_batch_size=8,
        embed_limit=37,
    )

    assert calls["trickle"] == (7, existing)
    assert calls["tag"] == (
        (1, 2),
        {"source": "lv", "item_ids": ["lv:1AJYWM"]},
    )
    assert calls["embed"] == (8, 37)
    assert summary["queries"] == 0
    assert summary["records"] == 1
    assert summary["inserted"] == 1
    assert summary["lv"] == trickle_report


@pytest.mark.parametrize(
    "extra_args, expected_limit",
    [([], 25), (["--limit", "4"], 4)],
)
def test_main_passes_lv_limit_to_ingest(
    monkeypatch, capsys, extra_args, expected_limit
):
    calls = {}

    def ingest_stub(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {"source": args[0]}

    monkeypatch.setattr(ingest, "ingest", ingest_stub)
    monkeypatch.setattr(
        ingest.sys,
        "argv",
        ["ingest", "--source", "lv", *extra_args],
    )

    ingest.main()

    assert json.loads(capsys.readouterr().out) == {"source": "lv"}
    assert calls["args"] == ("lv", 10, None)
    assert calls["kwargs"]["limit"] == expected_limit
