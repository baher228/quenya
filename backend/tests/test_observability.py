import asyncio
import logging
import re

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main, observability, phase2_api


@pytest.fixture
def observability_client(monkeypatch):
    monkeypatch.setattr(main.db, "init_db", lambda: None)
    monkeypatch.setattr(main, "QUENYA_WARM_CACHES", False)
    monkeypatch.setattr(phase2_api, "_owner_id", lambda: "test-owner")
    monkeypatch.setitem(
        main.app.dependency_overrides,
        phase2_api.current_user,
        lambda: {"id": "test-owner", "role": "tester"},
    )
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def access_caplog(caplog):
    access_logger = logging.getLogger("quenya.access")
    access_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger="quenya.access")
    yield caplog
    access_logger.removeHandler(caplog.handler)


def _stub_health(monkeypatch):
    monkeypatch.setattr(main.db, "total_counts", lambda: (0, 0))
    monkeypatch.setattr(main.jobs, "stats", lambda: {})
    monkeypatch.setattr(
        main.db,
        "schema_migration_status",
        lambda: {"version": None, "latest": "0001_baseline", "ok": False},
    )


def test_request_id_is_returned_and_valid_incoming_id_is_echoed(
    observability_client, monkeypatch
):
    _stub_health(monkeypatch)

    generated = observability_client.get("/api/health")
    echoed = observability_client.get(
        "/api/health",
        headers={"X-Request-ID": "incoming-request-123"},
    )

    assert re.fullmatch(r"[A-Za-z0-9-]{8,64}", generated.headers["X-Request-ID"])
    assert echoed.headers["X-Request-ID"] == "incoming-request-123"


def test_invalid_request_id_is_replaced(observability_client, monkeypatch):
    _stub_health(monkeypatch)

    response = observability_client.get(
        "/api/health",
        headers={"X-Request-ID": "bad id"},
    )

    assert re.fullmatch(r"[A-Za-z0-9-]{8,64}", response.headers["X-Request-ID"])
    assert response.headers["X-Request-ID"] != "bad id"


def test_access_log_uses_route_template_without_raw_path_or_query(
    observability_client, monkeypatch, access_caplog
):
    monkeypatch.setattr(main.jobs, "get", lambda *_args: None)
    path_id = "private-job-id-123"
    invite_token = "invite-secret-456"

    response = observability_client.get(
        f"/api/outfits/jobs/{path_id}?invite={invite_token}"
    )

    assert response.status_code == 404
    assert "route=/api/outfits/jobs/{job_id}" in access_caplog.text
    assert path_id not in access_caplog.text
    assert invite_token not in access_caplog.text


def test_server_error_is_logged_at_error(
    observability_client, monkeypatch, access_caplog
):
    def fail_get(*_args):
        raise RuntimeError("job database unavailable")

    monkeypatch.setattr(main.jobs, "get", fail_get)

    response = observability_client.get("/api/outfits/jobs/safe-job-id")

    assert response.status_code == 500
    assert any(
        record.name == "quenya.access"
        and record.levelno == logging.ERROR
        and "status=500" in record.message
        for record in access_caplog.records
    )


def test_call_next_exception_is_logged_then_reraised(access_caplog):
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/unmatched",
            "raw_path": b"/unmatched",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1234),
            "server": ("test", 80),
        }
    )

    async def fail(_request):
        raise RuntimeError("downstream failure")

    with pytest.raises(RuntimeError, match="downstream failure"):
        asyncio.run(main.request_timing(request, fail))

    assert any(
        record.name == "quenya.access"
        and record.levelno == logging.ERROR
        and "status=500" in record.message
        for record in access_caplog.records
    )


def test_init_sentry_is_disabled_without_dsn(monkeypatch):
    monkeypatch.delenv("QUENYA_SENTRY_DSN", raising=False)

    assert observability.init_sentry("test") is False


def test_before_send_scrubs_sensitive_request_data():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret",
                "cookie": "session=secret",
                "User-Agent": "Quenya/1",
            },
            "url": "https://app.quenya.co.uk/api/outfits?invite=secret#fragment",
            "query_string": "invite=secret",
        }
    }

    sanitized = observability.before_send(event, {})

    assert sanitized["request"]["headers"] == {"User-Agent": "Quenya/1"}
    assert sanitized["request"]["url"] == "https://app.quenya.co.uk/api/outfits"
    assert "query_string" not in sanitized["request"]
