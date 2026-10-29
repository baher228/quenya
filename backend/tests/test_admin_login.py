import sys

import pytest
from fastapi.testclient import TestClient

from app import accounts, db, main


@pytest.fixture
def isolated_admin_login_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-login.sqlite")
    db._INITIALIZED_DATABASES.clear()
    accounts.reset_admin_login_rate_limit()
    yield
    accounts.reset_admin_login_rate_limit()
    db._INITIALIZED_DATABASES.clear()


def _admin(name="Admin Owner", password="correct horse battery"):
    user, invite = accounts.create_user(name, "admin")
    if password is not None:
        accounts.set_password(user["id"], password)
    return user, invite


def _login(client, name, password, ip="192.0.2.10"):
    return client.post(
        "/api/auth/admin-login",
        json={"name": name, "password": password},
        headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"},
    )


def test_admin_login_success_and_case_insensitive_name(isolated_admin_login_db):
    user, _invite = _admin(name="  Admin Owner  ")
    with TestClient(main.app) as client:
        response = _login(client, " admin owner ", "correct horse battery")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"] == {"id": user["id"], "name": "  Admin Owner  ", "role": "admin"}
    assert accounts.user_for_session(body["token"])["id"] == user["id"]


def test_admin_login_rejects_wrong_password(isolated_admin_login_db):
    _admin()
    with TestClient(main.app) as client:
        response = _login(client, "Admin Owner", "wrong password")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong name or password"}


def test_admin_login_rejects_unknown_name(isolated_admin_login_db):
    with TestClient(main.app) as client:
        response = _login(client, "Nobody", "correct horse battery")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong name or password"}


@pytest.mark.parametrize("kind", ["tester", "revoked", "unset-password"])
def test_admin_login_rejects_ineligible_users(isolated_admin_login_db, kind):
    if kind == "tester":
        user, _invite = accounts.create_user("Admin Owner", "tester")
        accounts.set_password(user["id"], "correct horse battery")
    elif kind == "revoked":
        user, _invite = _admin()
        accounts.revoke_user(user["id"])
    else:
        _admin(password=None)

    with TestClient(main.app) as client:
        response = _login(client, "Admin Owner", "correct horse battery")
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong name or password"}


def test_admin_login_limits_failures_per_ip(isolated_admin_login_db):
    with TestClient(main.app) as client:
        for index in range(5):
            response = _login(client, f"unknown-{index}", "wrong", "198.51.100.4")
            assert response.status_code == 401
        limited = _login(client, "another-name", "wrong", "198.51.100.4")
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many attempts, try again later"}


def test_admin_login_uses_first_forwarded_for_hop(isolated_admin_login_db):
    with TestClient(main.app) as client:
        responses = [
            _login(
                client,
                f"unknown-{index}",
                "wrong",
                f"198.51.100.{index + 1}",
            )
            for index in range(6)
        ]
    assert [response.status_code for response in responses] == [401] * 6


def test_admin_login_limits_failures_per_normalized_name(isolated_admin_login_db):
    with TestClient(main.app) as client:
        for index in range(5):
            response = _login(
                client,
                "  ADMIN OWNER ",
                "wrong",
                f"198.51.100.{index + 1}",
            )
            assert response.status_code == 401
        limited = _login(client, "admin owner", "wrong", "198.51.100.20")
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many attempts, try again later"}


def test_successful_admin_login_resets_name_failure_counter(isolated_admin_login_db):
    _admin()
    with TestClient(main.app) as client:
        for index in range(4):
            assert _login(
                client,
                "Admin Owner",
                "wrong",
                f"198.51.100.{index + 1}",
            ).status_code == 401
        assert _login(
            client,
            " admin owner ",
            "correct horse battery",
            "198.51.100.10",
        ).status_code == 200
        for index in range(5):
            assert _login(
                client,
                "Admin Owner",
                "wrong",
                f"203.0.113.{index + 1}",
            ).status_code == 401
        limited = _login(client, "admin owner", "wrong", "203.0.113.20")
    assert limited.status_code == 429


def test_admin_password_change_requires_current_and_long_new_password(
    isolated_admin_login_db,
):
    user, invite = _admin()
    token = accounts.redeem_invite(invite)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(main.app) as client:
        wrong_current = client.post(
            "/api/admin/password",
            json={"current_password": "wrong password", "new_password": "new password long"},
            headers=headers,
        )
        too_short = client.post(
            "/api/admin/password",
            json={"current_password": "correct horse battery", "new_password": "short"},
            headers=headers,
        )
        changed = client.post(
            "/api/admin/password",
            json={
                "current_password": "correct horse battery",
                "new_password": "a newer password",
            },
            headers=headers,
        )
    assert wrong_current.status_code == 403
    assert too_short.status_code == 400
    assert changed.status_code == 200
    assert changed.json() == {"ok": True}
    with db.db_connection() as connection:
        stored = connection.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()["password_hash"]
    assert accounts.verify_password(stored, "a newer password")
    assert not accounts.verify_password(stored, "correct horse battery")


def test_set_password_rejects_short_password(isolated_admin_login_db):
    user, _invite = accounts.create_user("Admin Owner", "admin")
    with pytest.raises(ValueError, match="at least 12"):
        accounts.set_password(user["id"], "too short")


def test_set_password_migrates_existing_users_table(isolated_admin_login_db):
    with db.db_connection() as connection:
        connection.execute(
            """CREATE TABLE users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'tester', invite_hash TEXT UNIQUE,
                created_at TEXT NOT NULL, revoked_at TEXT
            )"""
        )
    db.init_db()
    with db.db_connection() as connection:
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(users)")
        }
    assert columns["password_hash"]["notnull"] == 0


def test_admin_password_cli_generate_prints_only_generated_password(
    isolated_admin_login_db,
    monkeypatch,
    capsys,
):
    user, _invite = accounts.create_user("CLI Admin", "admin")
    monkeypatch.setattr(
        sys,
        "argv",
        ["accounts", "set-password", "--name", "CLI Admin", "--generate"],
    )
    accounts.main()
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    generated = output.strip()
    assert generated
    with db.db_connection() as connection:
        stored = connection.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()["password_hash"]
    assert accounts.verify_password(stored, generated)


def test_invite_login_still_creates_a_session_after_refactor(isolated_admin_login_db):
    user, invite = accounts.create_user("Invite User")
    with TestClient(main.app) as client:
        response = client.post("/api/auth/invite", json={"token": invite})
    assert response.status_code == 200
    assert response.json()["user"] == {
        "id": user["id"],
        "name": "Invite User",
        "role": "tester",
    }
    assert accounts.user_for_session(response.json()["token"])["id"] == user["id"]
