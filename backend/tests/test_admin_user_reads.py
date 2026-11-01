from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app import accounts, db, main


@pytest.fixture
def admin_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-reads.sqlite")
    monkeypatch.setattr(db, "_maybe_embed_brief", lambda *_: None)
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    yield
    db._INITIALIZED_DATABASES.clear()


def _style(profile_id, owner_id, *, kind="user"):
    db.create_profile(profile_id, {"text": profile_id}, owner_id=owner_id, kind=kind)
    db.save_taste_version(profile_id, 1, {"private": profile_id}, {}, None)


def _legacy_users():
    with db.db_connection() as connection:
        rows = connection.execute(
            """SELECT u.*, MAX(s.last_seen_at) AS last_seen_at
               FROM users u LEFT JOIN auth_sessions s ON s.user_id=u.id
               GROUP BY u.id ORDER BY u.created_at"""
        ).fetchall()
    result = []
    for row in rows:
        user = dict(row)
        user["styles"] = [
            {"id": style["id"], "label": style["label"]}
            for style in db.profile_boards("user", user["id"])
        ]
        with db.db_connection() as connection:
            user["swipe_count"] = connection.execute(
                """SELECT COUNT(*) FROM swipes WHERE profile_id IN (
                       SELECT id FROM profiles WHERE kind='user' AND owner_id=?)""",
                (user["id"],),
            ).fetchone()[0]
        result.append({key: user.get(key) for key in (
            "id", "name", "role", "created_at", "revoked_at", "last_seen_at",
            "styles", "swipe_count",
        )})
    return result


def test_admin_summary_matches_legacy_without_cross_owner_counts(admin_db):
    first, invite = accounts.create_user("First", "admin")
    second, _ = accounts.create_user("Revoked")
    empty, _ = accounts.create_user("No styles")
    accounts.redeem_invite(invite)
    accounts.redeem_invite(invite)
    accounts.revoke_user(second["id"])
    for profile_id, owner, kind in (
        ("first-new", first["id"], "user"),
        ("first-old", first["id"], "user"),
        ("second", second["id"], "user"),
        ("shared", first["id"], "core"),
        ("unowned", None, "user"),
        ("absent-owner", "absent", "user"),
    ):
        _style(profile_id, owner, kind=kind)
    for profile_id, count in (
        ("first-new", 2), ("first-old", 3), ("second", 7),
        ("shared", 11), ("unowned", 13), ("absent-owner", 17), ("absent-style", 19),
    ):
        for index in range(count):
            db.insert_swipe(profile_id, f"item-{index}", bool(index % 2))
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE profiles SET created_at='2026-01-01', updated_at=NULL WHERE id='first-old'"
        )
        connection.execute(
            "UPDATE profiles SET updated_at='2026-02-01' WHERE id='first-new'"
        )
    actual = accounts._admin_users()
    assert actual == _legacy_users()
    by_id = {user["id"]: user for user in actual}
    assert by_id[first["id"]]["styles"] == [
        {"id": "first-new", "label": "Your style"},
        {"id": "first-old", "label": "Your style"},
    ]
    assert by_id[first["id"]]["swipe_count"] == 5
    assert by_id[second["id"]]["swipe_count"] == 7
    assert by_id[second["id"]]["revoked_at"] is not None
    assert by_id[empty["id"]]["styles"] == []
    assert by_id[empty["id"]]["swipe_count"] == 0
    assert by_id[first["id"]]["last_seen_at"] is not None


@pytest.mark.parametrize("users,styles", [(0, 0), (1, 0), (1, 20), (30, 3)])
def test_admin_read_count_is_constant(monkeypatch, admin_db, users, styles):
    for index in range(users):
        user, _ = accounts.create_user(f"Owner {index}")
        for style in range(styles):
            _style(f"{index}-{style}", user["id"])
    original = db.db_connection
    queries = []
    connections = []

    class ObservedConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, *args):
            queries.append(query)
            return self.connection.execute(query, *args)

    @contextmanager
    def observed():
        connections.append(1)
        with original() as connection:
            yield ObservedConnection(connection)

    monkeypatch.setattr(db, "db_connection", observed)
    result = accounts._admin_users()
    assert len(result) == users
    assert all(len(user["styles"]) == styles for user in result)
    assert len(connections) == 1
    assert len(queries) == 2
    assert all("taste_versions" not in query and "brief_json" not in query for query in queries)


def test_admin_summary_still_requires_admin_and_hides_auth_fields(admin_db):
    admin, admin_invite = accounts.create_user("Admin", "admin")
    tester, tester_invite = accounts.create_user("Tester")
    admin_token = accounts.redeem_invite(admin_invite)
    tester_token = accounts.redeem_invite(tester_invite)
    _style("owned", tester["id"])
    with TestClient(main.app) as client:
        assert client.get("/api/admin/users").status_code == 401
        assert client.get("/api/admin/users", headers={
            "Authorization": f"Bearer {tester_token}", "User-Agent": "Quenya/1",
        }).status_code == 403
        response = client.get("/api/admin/users", headers={
            "Authorization": f"Bearer {admin_token}",
        })
    assert response.status_code == 200
    assert {user["id"] for user in response.json()} == {admin["id"], tester["id"]}
    assert all(set(user) == {
        "id", "name", "role", "created_at", "revoked_at", "last_seen_at",
        "styles", "swipe_count",
    } for user in response.json())
