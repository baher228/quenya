import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.sources import vinted


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.db, "init_db", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize("proxy_key", [None, ""])
def test_proxy_endpoint_is_disabled_without_key(client, monkeypatch, proxy_key):
    if proxy_key is None:
        monkeypatch.delenv("QUENYA_VINTED_PROXY_KEY", raising=False)
    else:
        monkeypatch.setenv("QUENYA_VINTED_PROXY_KEY", proxy_key)

    response = client.get(
        "/internal/vinted/search",
        params={"q": "coat", "gender": "w"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize("header", [None, "wrong"])
def test_proxy_endpoint_rejects_missing_or_wrong_key(client, monkeypatch, header):
    monkeypatch.setenv("QUENYA_VINTED_PROXY_KEY", "secret")
    headers = {} if header is None else {"X-Quenya-Proxy-Key": header}

    response = client.get(
        "/internal/vinted/search",
        params={"q": "coat", "gender": "w"},
        headers=headers,
    )

    assert response.status_code == 401


def test_proxy_endpoint_returns_items_from_search_direct(client, monkeypatch):
    monkeypatch.setenv("QUENYA_VINTED_PROXY_KEY", "secret")
    calls = []
    items = [{"id": "vinted:123"}]

    def fake_search_direct(query, gender, per_page, page):
        calls.append((query, gender, per_page, page))
        return items

    monkeypatch.setattr(vinted, "search_direct", fake_search_direct)
    monkeypatch.setattr(
        vinted,
        "search",
        lambda *_args, **_kwargs: pytest.fail("proxy endpoint must not call search"),
    )

    response = client.get(
        "/internal/vinted/search",
        params={"q": "vintage coat", "gender": "m", "per_page": 5, "page": 4},
        headers={"X-Quenya-Proxy-Key": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": items}
    assert calls == [("vintage coat", "m", 5, 4)]


def test_proxy_endpoint_returns_scraper_error_as_502(client, monkeypatch):
    monkeypatch.setenv("QUENYA_VINTED_PROXY_KEY", "secret")
    message = "scraper failed " + "x" * 400

    def fail_search_direct(*_args, **_kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(vinted, "search_direct", fail_search_direct)

    response = client.get(
        "/internal/vinted/search",
        params={"q": "coat", "gender": "u"},
        headers={"X-Quenya-Proxy-Key": "secret"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": message[:300]}


@pytest.mark.parametrize(
    "params",
    [
        {"q": "", "gender": "w"},
        {"q": "x" * 201, "gender": "w"},
        {"q": "coat", "gender": "other"},
        {"q": "coat", "gender": "w", "per_page": 0},
        {"q": "coat", "gender": "w", "per_page": 97},
        {"q": "coat", "gender": "w", "page": 0},
        {"q": "coat", "gender": "w", "page": 21},
    ],
)
def test_proxy_endpoint_validates_query_parameters(client, params):
    response = client.get("/internal/vinted/search", params=params)

    assert response.status_code == 422


def test_search_uses_configured_proxy(monkeypatch):
    monkeypatch.setenv("QUENYA_VINTED_PROXY_URL", "https://proxy.example/")
    monkeypatch.setenv("QUENYA_VINTED_PROXY_KEY", "secret")
    items = [{"id": "vinted:123"}]
    calls = {}

    def fake_get(url, **kwargs):
        calls.update(url=url, **kwargs)
        return httpx.Response(200, json={"items": items})

    monkeypatch.setattr(vinted.httpx, "get", fake_get)

    assert vinted.search("vintage coat", "w", per_page=7, page=4) == items
    assert calls == {
        "url": "https://proxy.example/internal/vinted/search",
        "params": {"q": "vintage coat", "gender": "w", "per_page": 7, "page": 4},
        "headers": {"X-Quenya-Proxy-Key": "secret"},
        "timeout": 60,
    }


def test_search_raises_for_non_successful_proxy_response(monkeypatch):
    monkeypatch.setenv("QUENYA_VINTED_PROXY_URL", "https://proxy.example")
    monkeypatch.setattr(
        vinted.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(403, text="blocked"),
    )

    with pytest.raises(RuntimeError, match="Vinted proxy HTTP 403: blocked"):
        vinted.search("coat", "w")


def test_search_uses_direct_scraper_without_proxy_url(monkeypatch):
    monkeypatch.delenv("QUENYA_VINTED_PROXY_URL", raising=False)
    items = [{"id": "vinted:123"}]
    calls = []

    def fake_search_direct(query, gender, per_page, page):
        calls.append((query, gender, per_page, page))
        return items

    monkeypatch.setattr(vinted, "search_direct", fake_search_direct)

    assert vinted.search("coat", "u", per_page=4, page=3) == items
    assert calls == [("coat", "u", 4, 3)]


def test_search_direct_retries_once_after_a_transient_failure(monkeypatch):
    calls = []
    sleeps = []

    class Scraper:
        def search(self, params):
            calls.append(params)
            if len(calls) == 1:
                raise RuntimeError("temporary failure")
            return ["raw-item"]

    monkeypatch.setattr(vinted, "VintedScraper", lambda _url: Scraper())
    monkeypatch.setattr(vinted.time, "sleep", sleeps.append)
    monkeypatch.setattr(vinted, "normalize_item", lambda item, *_args: item)

    assert vinted.search_direct("coat", "w", per_page=20, page=2) == ["raw-item"]
    assert calls == [
        {"search_text": "coat", "per_page": 20, "page": 2},
        {"search_text": "coat", "per_page": 20, "page": 2},
    ]
    assert sleeps == [1]


def test_search_direct_raises_after_two_failures(monkeypatch):
    calls = []
    sleeps = []

    class Scraper:
        def search(self, params):
            calls.append(params)
            raise RuntimeError("still failing")

    monkeypatch.setattr(vinted, "VintedScraper", lambda _url: Scraper())
    monkeypatch.setattr(vinted.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="still failing"):
        vinted.search_direct("coat", "w", page=2)

    assert len(calls) == 2
    assert sleeps == [1]
