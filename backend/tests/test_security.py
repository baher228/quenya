"""Security headers and API rate-limit helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main, rate_limit, security
from app.security import enforce_user_rate_limit


@pytest.fixture
def security_client(monkeypatch):
    monkeypatch.setattr(main.db, "init_db", lambda: None)
    monkeypatch.setattr(main, "QUENYA_WARM_CACHES", False)
    monkeypatch.setattr(main.db, "total_counts", lambda: (0, 0))
    monkeypatch.setattr(main.jobs, "stats", lambda: {})
    monkeypatch.setattr(
        main.db,
        "schema_migration_status",
        lambda: {"version": "0005_cost_indexes", "latest": "0005_cost_indexes", "ok": True},
    )
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client


def test_security_headers_on_health(security_client):
    response = security_client.get("/api/health")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert "camera=()" in response.headers.get("permissions-policy", "")


def test_enforce_user_rate_limit_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(rate_limit.db, "DB_PATH", tmp_path / "rate.sqlite")
    rate_limit.db._INITIALIZED_DATABASES.clear()
    rate_limit.reset_bucket()
    monkeypatch.setenv("QUENYA_API_RATE_WINDOW_S", "60")
    for _ in range(3):
        enforce_user_rate_limit("user-1", "deck", limit=3)
    with pytest.raises(HTTPException) as exc:
        enforce_user_rate_limit("user-1", "deck", limit=3)
    assert exc.value.status_code == 429
    # Other users are unaffected.
    enforce_user_rate_limit("user-2", "deck", limit=3)


def test_security_header_defaults_stable():
    assert security.SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"


def test_enforce_user_rate_limit_fails_open_on_db_error(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(security.rate_limit, "check_and_hit", boom)

    enforce_user_rate_limit("owner-1", "deck", limit=1)


def test_rate_limit_count_ignores_expired_without_deleting(monkeypatch, tmp_path):
    monkeypatch.setattr(rate_limit.db, "DB_PATH", tmp_path / "rate.sqlite")
    monkeypatch.setattr(rate_limit, "PRUNE_PROBABILITY", 0.0)
    rate_limit.reset_bucket()

    rate_limit.hit("b", now_iso="2026-01-01T00:00:00+00:00", window_seconds=60)
    rate_limit.hit("b", now_iso="2026-01-01T00:05:00+00:00", window_seconds=60)

    assert not rate_limit.is_limited(
        "b", limit=2, window_seconds=60, now_iso="2026-01-01T00:05:30+00:00"
    )
    assert rate_limit.is_limited(
        "b", limit=1, window_seconds=60, now_iso="2026-01-01T00:05:30+00:00"
    )

    rate_limit.prune("b", window_seconds=60, now_iso="2026-01-01T00:05:30+00:00")
    assert not rate_limit.is_limited(
        "b", limit=2, window_seconds=3600, now_iso="2026-01-01T00:05:30+00:00"
    )
