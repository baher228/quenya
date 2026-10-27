from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient

from app import accounts, db, main, phase2_api
from app.schemas import ProfileBrief, RouteOut


def _profile(owner_id: str, profile_id: str, *, active: bool = True) -> None:
    db.create_profile(
        profile_id,
        {
            "text": f"Style {profile_id}",
            "gender": "womenswear",
            "looking_for": "outfits",
            "pieces": [],
        },
        active=active,
        owner_id=owner_id,
    )


def _user_and_session(name: str, role: str = "tester") -> tuple[dict, str]:
    user, invite = accounts.create_user(name, role)
    return user, accounts.redeem_invite(invite)


@pytest.fixture
def isolated_accounts_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "accounts.sqlite")
    db._INITIALIZED_DATABASES.clear()
    yield
    db._INITIALIZED_DATABASES.clear()


def test_authentication_required_but_health_stays_public(isolated_accounts_db):
    with TestClient(main.app) as client:
        for path in ("/api/boards", "/api/deck"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.json() == {"detail": "Sign in with your invite link"}
        assert client.get("/api/health").status_code == 200


def test_owner_lookup_fails_closed_inside_unauthenticated_http_request(
    isolated_accounts_db,
):
    router = APIRouter()

    @router.get("/_test-owner")
    def test_owner() -> dict[str, str | None]:
        return {"owner": phase2_api._owner_id()}

    main.app.include_router(router)
    with TestClient(main.app) as client:
        response = client.get("/_test-owner")
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in with your invite link"}


def test_direct_board_handler_keeps_legacy_fallback(isolated_accounts_db):
    assert phase2_api.boards() == []


def test_invite_rotation_sessions_revocation_and_logout_are_scoped(isolated_accounts_db):
    user, invite = accounts.create_user("Invite test")
    session = accounts.redeem_invite(invite)
    assert accounts.user_for_session(session)["id"] == user["id"]
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT invite_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()
    assert row["invite_hash"] == accounts._hash(invite)
    assert row["invite_hash"] != invite
    try:
        accounts.redeem_invite("bad-token")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid invite should be rejected")

    rotated = accounts.rotate_invite(user["id"])
    try:
        accounts.redeem_invite(invite)
    except ValueError:
        pass
    else:
        raise AssertionError("rotated invite should be invalid")
    second_session = accounts.redeem_invite(rotated)
    assert accounts.user_for_session(session)["id"] == user["id"]
    assert accounts.user_for_session(second_session)["id"] == user["id"]

    with TestClient(main.app) as client:
        response = client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {session}"},
        )
        assert response.status_code == 200
    assert accounts.user_for_session(session) is None
    assert accounts.user_for_session(second_session)["id"] == user["id"]

    accounts.revoke_user(user["id"])
    assert accounts.user_for_session(second_session) is None
    try:
        accounts.redeem_invite(rotated)
    except ValueError:
        pass
    else:
        raise AssertionError("revoked user invite should be invalid")


def test_users_only_see_and_activate_their_own_boards(isolated_accounts_db):
    user_a, token_a = _user_and_session("A")
    user_b, token_b = _user_and_session("B")
    _profile(user_a["id"], "board-a")
    _profile(user_b["id"], "board-b")

    with TestClient(main.app) as client:
        boards_a = client.get(
            "/api/boards", headers={"Authorization": f"Bearer {token_a}"}
        )
        boards_b = client.get(
            "/api/boards", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert [board["id"] for board in boards_a.json()] == ["board-a"]
        assert [board["id"] for board in boards_b.json()] == ["board-b"]
        assert client.post(
            "/api/boards/board-a/activate",
            headers={"Authorization": f"Bearer {token_b}"},
        ).status_code == 404
    assert db.active_profile("user", user_a["id"])["id"] == "board-a"
    assert db.active_profile("user", user_b["id"])["id"] == "board-b"


def test_database_initialization_keeps_one_active_board_per_owner(isolated_accounts_db):
    user_a, _ = _user_and_session("A")
    user_b, _ = _user_and_session("B")
    _profile(user_a["id"], "board-a")
    _profile(user_b["id"], "board-b")

    db._INITIALIZED_DATABASES.clear()
    db.init_db()

    assert db.active_profile("user", user_a["id"])["id"] == "board-a"
    assert db.active_profile("user", user_b["id"])["id"] == "board-b"


def test_tester_style_cap_covers_board_and_route_creation(monkeypatch, isolated_accounts_db):
    user, token = _user_and_session("One style")
    _profile(user["id"], "existing-style")
    try:
        phase2_api._ensure_can_create_style(user)
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "Tester accounts have one style"
    else:
        raise AssertionError("tester with a style should be capped")

    brief = ProfileBrief(
        text="Second style",
        looking_for="outfits",
        gender="womenswear",
        condition="both",
        niche=0.5,
        budget_gbp=200,
    )
    monkeypatch.setattr(
        phase2_api,
        "route_input",
        lambda *_args, **_kwargs: (
            RouteOut(
                context="other",
                board_id=None,
                label="Second",
                confidence=0.8,
                reason="test",
            ),
            None,
            SimpleNamespace(request_id=None),
            "",
        ),
    )
    with TestClient(main.app) as client:
        me_response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_response.status_code == 200, me_response.text
        response = client.post(
            "/api/profile",
            json={"brief": brief.model_dump(), "new_board": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Tester accounts have one style"}

    route_id = db.create_route(
        "user",
        "brief",
        "Another style",
        {
            "context": "other",
            "label": "Another",
            "_routed": "existing",
            "input_brief": {"text": "Another style"},
            "_previous_brief": {"text": "Original"},
        },
        None,
        "existing-style",
        user["id"],
    )
    with TestClient(main.app) as client:
        response = client.post(
            f"/api/route/{route_id}/correct",
            json={"label": "Separate"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "Tester accounts have one style"}

    user_zero, _ = _user_and_session("Zero styles")
    monkeypatch.setattr(phase2_api, "create_taste", lambda *_args: None)
    accounts._user_context.set(user_zero)
    created = phase2_api._new_board(
        {"text": "First", "gender": "womenswear", "looking_for": "outfits"},
        "First",
        "other",
    )
    assert created["owner_id"] == user_zero["id"]
    admin, _ = _user_and_session("Admin", "admin")
    accounts._user_context.set(admin)
    phase2_api._new_board(
        {"text": "Admin one", "gender": "womenswear", "looking_for": "outfits"},
        "One",
        "other",
    )
    second = phase2_api._new_board(
        {"text": "Admin two", "gender": "womenswear", "looking_for": "outfits"},
        "Two",
        "other",
    )
    assert second["owner_id"] == admin["id"]
    accounts.clear_context()


def test_foreign_object_ids_are_hidden_from_other_testers(isolated_accounts_db):
    user_a, _ = _user_and_session("Owner")
    user_b, token_b = _user_and_session("Other")
    _profile(user_a["id"], "owner-board")
    _profile(user_b["id"], "other-board")
    upload_id = db.add_upload(None, "image.jpg", {"one_line": "Look"}, None, user_a["id"])
    route_id = db.create_route(
        "user", "brief", "Owner brief", {}, None, "owner-board", user_a["id"]
    )
    job_id = "owner-job"
    db.save_fetch_job(job_id, "owner-board", {"id": job_id, "status": "done"})
    outfit_id = db.save_outfit(
        "owner-board",
        1,
        {"title": "Owner look", "mood": "Easy", "why": "Good"},
        [],
        0,
        None,
        outfit_id="owner-outfit",
    )

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {token_b}"}
        assert client.post(
            f"/api/route/{route_id}/correct", json={"label": "X"}, headers=headers
        ).status_code == 404
        assert client.get(f"/api/fetch/{job_id}", headers=headers).status_code == 404
        assert client.post(
            f"/api/uploads/{upload_id}/assign", json={}, headers=headers
        ).status_code == 404
        assert client.post(
            f"/api/outfits/{outfit_id}/swap",
            json={"item_id": "not-relevant"},
            headers=headers,
        ).status_code == 404
        assert client.post(
            f"/api/outfits/{outfit_id}/verdict",
            json={"verdict": "reject"},
            headers=headers,
        ).status_code == 404
        assert client.post(
            "/api/checkout",
            json={"outfit_id": outfit_id},
            headers=headers,
        ).status_code == 404


def test_reset_deletes_only_callers_data(isolated_accounts_db):
    owner, token = _user_and_session("Reset owner")
    other, _ = _user_and_session("Keep owner")
    _profile(owner["id"], "reset-board")
    _profile(other["id"], "keep-board")
    db.add_upload("reset-board", "reset.jpg", {}, None, owner["id"])
    db.add_upload("keep-board", "keep.jpg", {}, None, other["id"])
    db.create_route("user", "brief", "reset", {}, None, "reset-board", owner["id"])
    db.create_route("user", "brief", "keep", {}, None, "keep-board", other["id"])
    db.save_fetch_job("reset-job", "reset-board", {"id": "reset-job"})
    db.save_fetch_job("keep-job", "keep-board", {"id": "keep-job"})
    with db.db_connection() as connection:
        connection.execute(
            "INSERT INTO deck_events (id,profile_id,item_id,arm,policy,position,score,created_at) "
            "VALUES ('reset-event','reset-board','item','tags','test',1,0,'now')"
        )
        connection.execute(
            "INSERT INTO deck_events (id,profile_id,item_id,arm,policy,position,score,created_at) "
            "VALUES ('keep-event','keep-board','item','tags','test',1,0,'now')"
        )
    with TestClient(main.app) as client:
        response = client.post(
            "/api/reset", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert db.get_profile("reset-board") is None
    assert db.get_profile("keep-board") is not None
    assert db.get_fetch_job("reset-job") is None
    assert db.get_fetch_job("keep-job") is not None
    with db.db_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM deck_events WHERE id='reset-event'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM deck_events WHERE id='keep-event'"
        ).fetchone()[0] == 1


def test_testers_cannot_access_admin_routes(isolated_accounts_db):
    _user, token = _user_and_session("Tester")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(main.app) as client:
        responses = [
            client.get("/api/admin/users", headers=headers),
            client.post("/api/admin/invites", json={"name": "X"}, headers=headers),
            client.post("/api/admin/users/missing/rotate-invite", headers=headers),
            client.post("/api/admin/users/missing/revoke", headers=headers),
        ]
    assert [response.status_code for response in responses] == [403, 403, 403, 403]


def test_admin_user_listing_includes_styles_and_swipe_count(isolated_accounts_db):
    _admin, admin_token = _user_and_session("Admin", role="admin")
    tester, _tester_token = _user_and_session("Tester")
    _profile(tester["id"], "tester-style")
    db.insert_swipe("tester-style", "swiped-item", True)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200, response.text
    api_tester = next(user for user in response.json() if user["id"] == tester["id"])
    assert len(api_tester["styles"]) == 1
    assert api_tester["swipe_count"] == 1

    cli_tester = next(
        user for user in accounts._admin_users() if user["id"] == tester["id"]
    )
    assert len(cli_tester["styles"]) == 1
    assert cli_tester["swipe_count"] == 1
