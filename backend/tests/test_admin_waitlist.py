from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import accounts, db, main
from app.waitlist import reset_rate_limit


@pytest.fixture
def isolated_waitlist_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-waitlist.sqlite")
    db._INITIALIZED_DATABASES.clear()
    reset_rate_limit()
    yield
    reset_rate_limit()
    db._INITIALIZED_DATABASES.clear()


def _session(role="tester"):
    user, invite = accounts.create_user("Admin" if role == "admin" else "Tester", role)
    return user, accounts.redeem_invite(invite)


def _signup(client, email="ada@example.com", **extra):
    return client.post(
        "/api/waitlist",
        json={"email": email, "consent": True, **extra},
    )


def test_admin_waitlist_routes_require_admin(isolated_waitlist_db):
    _user, tester_token = _session()
    headers = {"Authorization": f"Bearer {tester_token}"}
    with TestClient(main.app) as client:
        unauthenticated = [
            client.get("/api/admin/waitlist"),
            client.post("/api/admin/waitlist/missing/approve"),
            client.post("/api/admin/waitlist/missing/reject"),
        ]
        forbidden = [
            client.get("/api/admin/waitlist", headers=headers),
            client.post("/api/admin/waitlist/missing/approve", headers=headers),
            client.post("/api/admin/waitlist/missing/reject", headers=headers),
        ]
    assert [response.status_code for response in unauthenticated] == [401] * 3
    assert [response.status_code for response in forbidden] == [403] * 3


def test_admin_waitlist_filters_and_orders_newest_first(isolated_waitlist_db):
    _admin, token = _session("admin")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(main.app) as client:
        _signup(
            client, "pending@example.com", name="Pending", devices=["web"],
            style_text="Soft tailoring", handle="@pending",
        )
        _signup(client, "approved@example.com", name="Approved")
        _signup(client, "rejected@example.com", name="Rejected")
        with db.db_connection() as connection:
            ids = {
                row["email"]: row["id"]
                for row in connection.execute("SELECT id,email FROM waitlist")
            }
        assert client.post(
            f"/api/admin/waitlist/{ids['approved@example.com']}/approve",
            headers=headers,
        ).status_code == 200
        assert client.post(
            f"/api/admin/waitlist/{ids['rejected@example.com']}/reject",
            headers=headers,
        ).status_code == 200
        with db.db_connection() as connection:
            connection.execute(
                """UPDATE waitlist SET created_at=CASE email
                       WHEN 'pending@example.com' THEN '2025-01-01T00:00:00'
                       WHEN 'approved@example.com' THEN '2025-01-02T00:00:00'
                       ELSE '2025-01-03T00:00:00' END"""
            )
        listed = client.get("/api/admin/waitlist?status=all", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [row["email"] for row in listed.json()] == [
            "rejected@example.com",
            "approved@example.com",
            "pending@example.com",
        ]
        pending = client.get("/api/admin/waitlist", headers=headers).json()
        assert [row["email"] for row in pending] == ["pending@example.com"]
        approved = client.get(
            "/api/admin/waitlist?status=approved", headers=headers
        ).json()
        assert [row["email"] for row in approved] == ["approved@example.com"]
        rejected = client.get(
            "/api/admin/waitlist?status=rejected", headers=headers
        ).json()
        assert [row["email"] for row in rejected] == ["rejected@example.com"]
        approved_row = approved[0]
        assert approved_row["user_id"]
        assert approved_row["reviewed_at"]
        row = next(row for row in listed.json() if row["email"] == "pending@example.com")
        assert set(row) == {
            "id", "email", "name", "handle", "style_text", "wants_testing",
            "devices", "source", "created_at", "status", "reviewed_at", "user_id",
        }
        assert row["status"] == "pending"
        assert row["handle"] == "@pending"
        assert row["style_text"] == "Soft tailoring"
        assert row["devices"] == "web"


def test_approval_creates_tester_invite_and_duplicate_approval_conflicts(
    isolated_waitlist_db,
):
    _admin, admin_token = _session("admin")
    headers = {"Authorization": f"Bearer {admin_token}"}
    with TestClient(main.app) as client:
        _signup(client, name="Ada Lovelace")
        with db.db_connection() as connection:
            waitlist_id = connection.execute(
                "SELECT id FROM waitlist WHERE email='ada@example.com'"
            ).fetchone()["id"]

        approved = client.post(
            f"/api/admin/waitlist/{waitlist_id}/approve", headers=headers
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["user"]["name"] == "Ada Lovelace"
        assert body["user"]["role"] == "tester"
        token = parse_qs(urlsplit(body["invite_url"]).query)["invite"][0]
        session = accounts.redeem_invite(token)
        me = client.get("/api/me", headers={"Authorization": f"Bearer {session}"})
        assert me.status_code == 200
        assert me.json()["role"] == "tester"
        assert me.json()["max_styles"] == 1

        with db.db_connection() as connection:
            row = connection.execute(
                "SELECT status, user_id, reviewed_at FROM waitlist WHERE id=?",
                (waitlist_id,),
            ).fetchone()
            assert row["status"] == "approved"
            assert row["user_id"] == body["user"]["id"]
            assert row["reviewed_at"]
            assert connection.execute(
                "SELECT COUNT(*) FROM users WHERE role='tester'"
            ).fetchone()[0] == 1
        _signup(client, name="Ada L.", handle="@ada")
        with db.db_connection() as connection:
            row_after_resubmit = connection.execute(
                "SELECT status, user_id, reviewed_at FROM waitlist WHERE id=?",
                (waitlist_id,),
            ).fetchone()
            assert row_after_resubmit["status"] == "approved"
            assert row_after_resubmit["user_id"] == body["user"]["id"]
            assert row_after_resubmit["reviewed_at"] == row["reviewed_at"]

        duplicate = client.post(
            f"/api/admin/waitlist/{waitlist_id}/approve", headers=headers
        )
        assert duplicate.status_code == 409
        with db.db_connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM users WHERE role='tester'"
            ).fetchone()[0] == 1


def test_concurrent_approvals_create_only_one_tester(isolated_waitlist_db):
    _admin, admin_token = _session("admin")
    headers = {"Authorization": f"Bearer {admin_token}"}
    with TestClient(main.app) as client:
        _signup(client, name="Ada Lovelace")
        with db.db_connection() as connection:
            waitlist_id = connection.execute(
                "SELECT id FROM waitlist WHERE email='ada@example.com'"
            ).fetchone()["id"]

        barrier = Barrier(2)

        def approve():
            barrier.wait()
            return client.post(
                f"/api/admin/waitlist/{waitlist_id}/approve",
                headers=headers,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: approve(), range(2)))

        assert sorted(response.status_code for response in responses) == [200, 409]
        with db.db_connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM users WHERE role='tester'"
            ).fetchone()[0] == 1


def test_reject_and_resubmission_preserve_review_state(isolated_waitlist_db):
    _admin, token = _session("admin")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(main.app) as client:
        _signup(client, name="Grace")
        with db.db_connection() as connection:
            waitlist_id = connection.execute(
                "SELECT id FROM waitlist WHERE email='ada@example.com'"
            ).fetchone()["id"]
        rejected = client.post(
            f"/api/admin/waitlist/{waitlist_id}/reject", headers=headers
        )
        assert rejected.status_code == 200
        _signup(client, name="Grace Hopper", handle="@grace")
        with db.db_connection() as connection:
            row = connection.execute(
                "SELECT status, user_id, reviewed_at, name FROM waitlist WHERE id=?",
                (waitlist_id,),
            ).fetchone()
        assert row["status"] == "rejected"
        assert row["user_id"] is None
        assert row["reviewed_at"]
        assert row["name"] == "Grace Hopper"
