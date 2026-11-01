import logging
import re
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app import main
from app.request_timing import RequestTimingMiddleware


def test_route_timings_use_templates_and_never_log_user_data(caplog):
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.post("/boards/{board_id}/outfits")
    def outfits(board_id: str):
        return {"board_id": board_id}

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = TestClient(app).post(
            "/boards/private-board/outfits?invite=private-token",
            headers={"Authorization": "Bearer private-token"},
            json={"note": "private-body"},
        )
    assert response.status_code == 200
    assert re.fullmatch(r"app;dur=\d+\.\d\d", response.headers["server-timing"])
    assert "method=POST route=/boards/{board_id}/outfits status=200" in caplog.text
    for secret in ("private-board", "private-token", "private-body", "invite="):
        assert secret not in caplog.text
    assert caplog.text.count("http_request") == 1


def test_timing_records_failed_and_unmatched_requests_without_raw_paths(caplog):
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/broken/{board_id}")
    def broken(board_id: str):
        raise ValueError("private-exception-content")

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/broken/private-board").status_code == 500
        assert client.get("/private-path").status_code == 404
    assert "status=500" in caplog.text
    assert "complete=False" in caplog.text
    assert "route=unmatched status=404" in caplog.text
    assert "private-board" not in caplog.text
    assert "private-path" not in caplog.text
    assert "private-exception-content" not in caplog.text


def test_streaming_timings_include_body_completion(caplog):
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter(["first", "last"]))

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = TestClient(app).get("/stream")
    assert response.text == "firstlast"
    assert "route=/stream status=200" in caplog.text
    assert "complete=True" in caplog.text


def test_live_and_ready_do_not_need_catalogue_counts(monkeypatch):
    calls = []

    @contextmanager
    def connection():
        class Connection:
            def execute(self, sql):
                calls.append(sql)
                return self

            def fetchone(self):
                return (1,)

        yield Connection()

    monkeypatch.setattr(main.db, "db_connection", connection)
    monkeypatch.setattr(main.db, "schema_migration_status", lambda: {"ok": True})
    monkeypatch.setattr(main.db, "total_counts", lambda: (_ for _ in ()).throw(
        AssertionError("catalogue count should not run")))
    monkeypatch.setattr(main.jobs, "stats", lambda: (_ for _ in ()).throw(
        AssertionError("job statistics should not run")))
    client = TestClient(main.app)
    assert client.get("/api/live").json() == {"ok": True}
    assert client.get("/api/ready").json() == {"ok": True}
    assert calls == ["SELECT 1 FROM profiles LIMIT 1"]


def test_ready_fails_closed_without_database_or_migrations(monkeypatch):
    @contextmanager
    def unavailable():
        raise OSError("sensitive-connection-info")
        yield

    monkeypatch.setattr(main.db, "db_connection", unavailable)
    client = TestClient(main.app)
    assert client.get("/api/live").json() == {"ok": True}
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert "sensitive-connection-info" not in response.text

    @contextmanager
    def available():
        class Connection:
            def execute(self, _sql):
                return self

            def fetchone(self):
                return (1,)

        yield Connection()

    monkeypatch.setattr(main.db, "db_connection", available)
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(main.db, "schema_migration_status", lambda: {"ok": False})
    assert client.get("/api/ready").status_code == 503


def test_sqlite_readiness_checks_the_actual_schema(monkeypatch, tmp_path):
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.setattr(main.db, "DB_PATH", tmp_path / "ready.sqlite")
    monkeypatch.setitem(main.db._POSTGRES_SCHEMA_STATUS, "ok", False)
    client = TestClient(main.app)
    assert client.get("/api/ready").status_code == 503
    main.db.init_db()
    assert client.get("/api/ready").json() == {"ok": True}
