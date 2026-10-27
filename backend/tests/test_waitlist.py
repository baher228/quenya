import csv
import io
import os

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.waitlist import export_waitlist, reset_rate_limit


@pytest.fixture(autouse=True)
def isolate_waitlist_db(monkeypatch, tmp_path):
    reset_rate_limit()
    if not os.getenv("QUENYA_TEST_PG_URL"):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "waitlist.sqlite")
        db._INITIALIZED_DATABASES.clear()
    init_db = db.init_db
    monkeypatch.setattr(main.db, "init_db", lambda: init_db())
    yield
    reset_rate_limit()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _rows():
    with db.db_connection() as connection:
        return connection.execute("SELECT * FROM waitlist").fetchall()


def test_valid_signup_stores_normalized_signup(client):
    response = client.post(
        "/api/waitlist",
        json={
            "email": "  Ada@Example.COM ",
            "name": " Ada Lovelace ",
            "devices": ["iphone", "web", "iphone"],
            "consent": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["email"] == "ada@example.com"
    assert rows[0]["name"] == "Ada Lovelace"
    assert rows[0]["devices"] == "iphone,web"
    assert rows[0]["wants_testing"] == 1
    assert rows[0]["consent_at"]


@pytest.mark.parametrize(
    "payload",
    [
        {"consent": True},
        {"email": "not-an-email", "consent": True},
        {"email": "ada@example.com", "consent": False},
        {"email": "ada@example.com"},
        {
            "email": "ada@example.com",
            "consent": True,
            "devices": ["blackberry"],
        },
        {"email": "ada@example.com", "consent": True, "name": "a" * 81},
        {"email": "ada@example.com", "consent": True, "unknown": "field"},
    ],
)
def test_invalid_payloads_are_rejected(client, payload):
    assert client.post("/api/waitlist", json=payload).status_code == 422
    assert _rows() == []


def test_signup_deduplicates_by_normalized_email_and_updates_fields(client):
    first = client.post(
        "/api/waitlist",
        json={
            "email": "Ada@Example.com",
            "name": "Ada",
            "handle": "first",
            "consent": True,
        },
    )
    first_row = _rows()[0]
    second = client.post(
        "/api/waitlist",
        json={
            "email": " ada@example.COM ",
            "handle": "second",
            "consent": True,
        },
    )

    assert first.status_code == second.status_code == 200
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["handle"] == "second"
    assert rows[0]["name"] == "Ada"
    assert rows[0]["created_at"] == first_row["created_at"]
    assert rows[0]["updated_at"] >= first_row["updated_at"]


def test_honeypot_returns_success_without_storing(client):
    response = client.post(
        "/api/waitlist",
        json={
            "email": "spam@example.com",
            "consent": True,
            "website": "http://spam",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _rows() == []


def test_rate_limit_is_per_forwarded_ip(client):
    payload = {"email": "ada@example.com", "consent": True}
    headers = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    responses = [
        client.post("/api/waitlist", json=payload, headers=headers)
        for _ in range(5)
    ]
    limited = client.post("/api/waitlist", json=payload, headers=headers)
    other_ip = client.post(
        "/api/waitlist",
        json=payload,
        headers={"X-Forwarded-For": "203.0.113.8"},
    )

    assert all(response.status_code == 200 for response in responses)
    assert limited.status_code == 429
    assert other_ip.status_code == 200
    assert limited.json()["detail"]["error"] == (
        "Too many signups from this address. Try again in a minute."
    )


def test_export_writes_header_and_rows(capsys):
    db.init_db()
    with db.db_connection() as connection:
        for email in ("ada@example.com", "grace@example.com"):
            connection.execute(
                """INSERT INTO waitlist (
                       id, email, wants_testing, created_at, updated_at, consent_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (email, email, 1, email, email, email),
            )

    export_waitlist()

    output = capsys.readouterr().out
    rows = list(csv.reader(io.StringIO(output)))
    assert rows[0] == [
        "id",
        "email",
        "name",
        "handle",
        "style_text",
        "wants_testing",
        "devices",
        "source",
        "user_agent",
        "created_at",
        "updated_at",
        "consent_at",
    ]
    assert len(rows) == 3
    assert {row[1] for row in rows[1:]} == {"ada@example.com", "grace@example.com"}
