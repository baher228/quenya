from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import accounts, db, main, phase2_api


@pytest.fixture
def board_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "board-reads.sqlite")
    monkeypatch.setattr(db, "_maybe_embed_brief", lambda *_args: None)
    db._INITIALIZED_DATABASES.clear()
    user_token = accounts._user_context.set(None)
    request_token = accounts._in_request.set(False)
    db.init_db()
    yield
    accounts._in_request.reset(request_token)
    accounts._user_context.reset(user_token)
    db._INITIALIZED_DATABASES.clear()


def _board(profile_id, owner_id=None, *, kind="user", active=False):
    db.create_profile(
        profile_id,
        {"text": f"Private brief for {profile_id}", "gender": "both"},
        owner_id=owner_id,
        kind=kind,
        active=active,
        label=f"Label {profile_id}",
        context="city",
    )


@contextmanager
def _read_counts(monkeypatch):
    counts = {"connections": 0, "queries": []}
    original_connect = db.connect

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, *args, **kwargs):
            counts["queries"].append(query)
            return self.connection.execute(query, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def connect():
        counts["connections"] += 1
        return Connection(original_connect())

    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", connect)
        yield counts


def _seed_summaries():
    owner_a, invite_a = accounts.create_user("A", "admin")
    owner_b, invite_b = accounts.create_user("B", "admin")
    for profile_id, owner, kind in (
        ("a-one", owner_a, "user"),
        ("a-two", owner_a, "user"),
        ("b-one", owner_b, "user"),
        ("unowned", None, "user"),
        ("shared", owner_a, "core"),
    ):
        _board(profile_id, owner["id"] if owner else None, kind=kind, active=True)
    for profile_id, versions in (("a-one", [3, 1, 2]), ("b-one", [8])):
        for version in versions:
            db.save_taste_version(profile_id, version, {"private": profile_id}, {}, None)
    for profile_id, swipe_count, outfit_count, upload_count in (
        ("a-one", 2, 3, 4),
        ("a-two", 1, 0, 0),
        ("b-one", 5, 2, 1),
        ("shared", 6, 1, 2),
    ):
        for index in range(swipe_count):
            db.insert_swipe(profile_id, f"item-{index}", bool(index % 2))
        with db.db_connection() as connection:
            for index in range(outfit_count):
                connection.execute(
                    """INSERT INTO outfits
                       (id, profile_id, taste_version, title, mood,
                        piece_ids_json, why, created_at)
                       VALUES (?, ?, 1, 'Outfit', 'City', '[]', 'Test', ?)""",
                    (f"{profile_id}-outfit-{index}", profile_id, db.now_iso()),
                )
            for index in range(upload_count):
                connection.execute(
                    """INSERT INTO uploads (id, profile_id, image_path, created_at)
                       VALUES (?, ?, 'test.jpg', ?)""",
                    (f"{profile_id}-upload-{index}", profile_id, db.now_iso()),
                )
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO uploads (id, image_path, created_at)
               VALUES ('unassigned', 'test.jpg', ?)""",
            (db.now_iso(),),
        )
        connection.execute(
            """UPDATE profiles SET created_at='2026-01-01', updated_at='2026-01-04'
               WHERE id='a-one'"""
        )
        connection.execute(
            """UPDATE profiles SET created_at='2026-01-03', updated_at=NULL
               WHERE id='a-two'"""
        )
    return owner_a, invite_a, owner_b, invite_b


@pytest.mark.parametrize("kind", ["user", "core"])
@pytest.mark.parametrize("owner", ["a", "b", "missing", None])
def test_board_summaries_match_legacy_response(board_db, kind, owner):
    owner_a, _, owner_b, _ = _seed_summaries()
    owner_id = {"a": owner_a["id"], "b": owner_b["id"], "missing": "absent"}.get(owner)
    expected = [
        phase2_api._board_summary(profile)
        for profile in db.profile_boards(kind, owner_id)
    ]
    assert db.profile_board_summaries(kind, owner_id) == expected
    assert db.profile_count(kind, owner_id) == len(expected)
    if kind == "user" and owner == "a":
        assert [board["id"] for board in expected] == ["a-one", "a-two"]
        assert expected[0]["version"] == 3
        assert expected[0]["counts"] == {"swipes": 2, "outfits": 3, "uploads": 4}
        assert expected[1]["version"] == 0
        assert expected[1]["counts"] == {"swipes": 1, "outfits": 0, "uploads": 0}


@pytest.mark.parametrize("board_count", [0, 1, 20, 100])
def test_startup_reads_use_one_statement_and_connection(monkeypatch, board_db, board_count):
    user, _ = accounts.create_user("Owner", "admin")
    for index in range(board_count):
        profile_id = f"board-{index}"
        _board(profile_id, user["id"])
        db.save_taste_version(profile_id, 1, {"private": "taste"}, {}, None)
    accounts._user_context.set(user)

    with _read_counts(monkeypatch) as counts:
        me = accounts.me(user)
    assert me["style_count"] == board_count
    assert me["max_styles"] is None
    assert counts["connections"] == 1
    assert len(counts["queries"]) == 1
    assert "COUNT(*)" in counts["queries"][0]
    assert "taste_versions" not in counts["queries"][0]

    with _read_counts(monkeypatch) as counts:
        boards = phase2_api.boards()
    assert len(boards) == board_count
    assert counts["connections"] == 1
    assert len(counts["queries"]) == 1
    assert "profile_json" not in counts["queries"][0]
    assert "signals_json" not in counts["queries"][0]


def test_startup_http_responses_are_owner_scoped(board_db):
    owner_a, invite_a, owner_b, invite_b = _seed_summaries()
    tokens = [accounts.redeem_invite(invite) for invite in (invite_a, invite_b)]
    with TestClient(main.app) as client:
        for user, token, profile_ids in (
            (owner_a, tokens[0], ["a-one", "a-two"]),
            (owner_b, tokens[1], ["b-one"]),
        ):
            headers = {"Authorization": f"Bearer {token}"}
            response = client.get("/api/boards", headers=headers)
            assert response.status_code == 200
            assert [board["id"] for board in response.json()] == profile_ids
            assert response.json() == [
                phase2_api._board_summary(profile)
                for profile in db.profile_boards("user", user["id"])
            ]
            response = client.get("/api/me", headers=headers)
            assert response.status_code == 200
            assert response.json() == {
                **accounts._public(user),
                "max_styles": None,
                "style_count": len(profile_ids),
            }


def test_board_summary_http_context_fails_closed_before_read(monkeypatch, board_db):
    accounts._in_request.set(True)
    with _read_counts(monkeypatch) as counts, pytest.raises(HTTPException) as error:
        phase2_api.boards()
    assert error.value.status_code == 401
    assert counts["connections"] == 0


def test_tester_style_count_excludes_other_users_and_core(board_db):
    tester, _ = accounts.create_user("Tester")
    other, _ = accounts.create_user("Other")
    _board("other", other["id"])
    _board("core", tester["id"], kind="core")
    assert accounts.me(tester)["style_count"] == 0
    assert accounts.me(tester)["max_styles"] == 1
    phase2_api._ensure_can_create_style(tester)
    _board("own", tester["id"])
    assert accounts.me(tester)["style_count"] == 1
    with pytest.raises(HTTPException) as error:
        phase2_api._ensure_can_create_style(tester)
    assert error.value.status_code == 403
