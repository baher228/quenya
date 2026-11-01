from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from threading import Thread

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import accounts, db, jobs, main, phase2_api


@pytest.fixture
def isolated_jobs_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    monkeypatch.setenv("QUENYA_JOB_RUNNER", "worker")
    monkeypatch.setattr(db, "_maybe_embed_brief", lambda *_args: None)
    db._INITIALIZED_DATABASES.clear()
    yield
    db._INITIALIZED_DATABASES.clear()


def _user_and_profile(name: str) -> tuple[dict, str]:
    user, invite = accounts.create_user(name)
    token = accounts.redeem_invite(invite)
    db.create_profile(
        f"profile-{user['id']}",
        {
            "text": "Relaxed workwear",
            "gender": "womenswear",
            "looking_for": "outfits",
            "pieces": [],
        },
        owner_id=user["id"],
    )
    return user, token


def test_sqlite_queue_lifecycle_claims_and_requeues_stale_jobs(isolated_jobs_db):
    job_id = jobs.enqueue("outfits", "owner", "profile", {"count": 2})

    claimed = jobs.claim_next(["outfits"], "worker-1")

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["payload"] == {"count": 2}
    assert jobs.claim_next(["outfits"], "worker-2") is None
    assert jobs.heartbeat(job_id)

    stale_at = (datetime.now(UTC) - timedelta(minutes=6)).isoformat()
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE jobs SET heartbeat_at=? WHERE id=?",
            (stale_at, job_id),
        )
    assert jobs.requeue_stale() == 1
    assert jobs.get(job_id, "owner")["status"] == "queued"

    claimed_again = jobs.claim_next(["outfits"], "worker-2")
    assert claimed_again["attempts"] == 2
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE jobs SET heartbeat_at=? WHERE id=?",
            (stale_at, job_id),
        )
    assert jobs.requeue_stale() == 1
    assert jobs.get(job_id, "owner")["error"] == {
        "status_code": 504,
        "detail": "Outfit job timed out",
    }


def test_sqlite_concurrent_claims_do_not_duplicate_jobs(isolated_jobs_db):
    job_ids = {
        jobs.enqueue("outfits", "owner", "profile", {"count": 1})
        for _ in range(8)
    }
    claimed = []

    def claim():
        while job := jobs.claim_next(["outfits"], "worker"):
            claimed.append(job["id"])

    threads = [Thread(target=claim) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == len(job_ids)
    assert set(claimed) == job_ids


def test_stats_reports_queue_depth_and_oldest_wait(isolated_jobs_db):
    queued_id = jobs.enqueue("outfits", "owner", "profile", {"count": 1})
    running_id = jobs.enqueue("outfits", "owner", "profile", {"count": 1})
    stale_created_at = (datetime.now(UTC) - timedelta(seconds=4)).isoformat()
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE jobs SET created_at=? WHERE id=?",
            (stale_created_at, queued_id),
        )
    assert jobs.claim(running_id, "worker") is not None

    result = jobs.stats()

    assert result["queued"] == 1
    assert result["running"] == 1
    assert result["oldest_queued_s"] >= 3


def test_postgres_claim_uses_skip_locked_update(monkeypatch):
    row = {
        "id": "job-1",
        "kind": "outfits",
        "owner_id": "owner",
        "profile_id": "profile",
        "status": "running",
        "payload_json": '{"count":1}',
        "result_json": None,
        "error_json": None,
        "attempts": 1,
        "worker_id": "worker",
        "created_at": datetime.now(UTC),
        "started_at": datetime.now(UTC),
        "heartbeat_at": datetime.now(UTC),
        "finished_at": None,
    }
    statements = []

    class Cursor:
        def fetchone(self):
            return row

    class Connection:
        def acquire_advisory_xact_lock(self, _key):
            pass

        def execute(self, statement, params=()):
            statements.append((statement, params))
            return Cursor()

    connection = Connection()

    @contextmanager
    def connection_context():
        yield connection

    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "db_connection", connection_context)

    claimed = jobs.claim_next(["outfits"], "worker")

    assert claimed["id"] == "job-1"
    assert claimed["payload"] == {"count": 1}
    statement, params = statements[0]
    assert statement.startswith("UPDATE jobs")
    assert "FOR UPDATE" in statement and "SKIP LOCKED" in statement
    assert "RETURNING *" in statement
    assert params == ("worker", ["outfits"])


def test_job_reads_are_owner_scoped(isolated_jobs_db):
    owner, owner_token = _user_and_profile("Owner")
    _other, other_token = _user_and_profile("Other")

    with TestClient(main.app) as client:
        created = client.post(
            "/api/outfits/jobs",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"count": 1},
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        owner_read = client.get(
            f"/api/outfits/jobs/{job_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        result = {"outfits": [], "taste_version": 1, "dropped": 0, "request_ids": []}
        jobs.complete(job_id, result)
        done_read = client.get(
            f"/api/outfits/jobs/{job_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        error_id = jobs.enqueue(
            "outfits",
            owner["id"],
            f"profile-{owner['id']}",
            {"count": 1},
        )
        jobs.fail(error_id, 502, "Outfit generation failed")
        error_read = client.get(
            f"/api/outfits/jobs/{error_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        other_read = client.get(
            f"/api/outfits/jobs/{job_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )

    assert owner_read.status_code == 200
    assert owner_read.json() == {"status": "queued"}
    assert done_read.json() == {"status": "done", "result": result}
    assert error_read.json() == {
        "status": "error",
        "error": {"status_code": 502, "detail": "Outfit generation failed"},
    }
    assert other_read.status_code == 404
    assert jobs.get(job_id, owner["id"]) is not None
    assert jobs.get(job_id, _other["id"]) is None


def test_job_status_requires_auth_and_blocks_browser_testers(isolated_jobs_db):
    owner, token = _user_and_profile("Browser")
    job_id = jobs.enqueue("outfits", owner["id"], f"profile-{owner['id']}", {})

    with TestClient(main.app) as client:
        unauthenticated = client.get(f"/api/outfits/jobs/{job_id}")
        browser = client.get(
            f"/api/outfits/jobs/{job_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "Mozilla/5.0",
            },
        )

    assert unauthenticated.status_code == 401
    assert browser.status_code == 403
    assert browser.json() == {"detail": accounts.APP_ONLY_DETAIL}


def test_worker_job_pins_active_board_before_it_runs(isolated_jobs_db, monkeypatch):
    owner, token = _user_and_profile("Pinned board")
    original_profile = f"profile-{owner['id']}"
    switched_profile = f"switched-{owner['id']}"
    brief = {
        "text": "Relaxed workwear",
        "gender": "womenswear",
        "looking_for": "outfits",
        "pieces": [],
    }
    db.create_profile(
        switched_profile,
        brief,
        active=False,
        owner_id=owner["id"],
    )
    db.activate_profile(original_profile, owner_id=owner["id"])

    with TestClient(main.app) as client:
        response = client.post(
            "/api/outfits/jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={"count": 1},
        )

    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    queued = jobs.get(job_id, owner["id"])
    assert queued["profile_id"] == original_profile
    assert queued["payload"]["board_id"] == original_profile

    db.activate_profile(switched_profile, owner_id=owner["id"])
    saved_profile_ids = []
    monkeypatch.setattr(
        phase2_api.db,
        "save_outfit",
        lambda profile_id, *_args: saved_profile_ids.append(profile_id) or "outfit",
    )

    def generate(body):
        phase2_api.db.save_outfit(body.board_id, None, None, None, None, None)
        return {"outfits": []}

    monkeypatch.setattr(phase2_api, "generate_outfits", generate)

    claimed = jobs.claim(job_id, "worker")
    jobs.run_job(claimed)

    assert saved_profile_ids == [original_profile]


def test_inline_runner_and_legacy_endpoint_return_generation_result(
    isolated_jobs_db, monkeypatch
):
    owner, token = _user_and_profile("Inline")
    from app.schemas import Outfit, StylistOutput

    db.save_taste_version(
        f"profile-{owner['id']}",
        1,
        {"price_ceiling_gbp": 100},
        {},
        "taste-request",
    )
    item = {"id": "item-1"}
    contexts = []

    def fake_call_task(*_args):
        contexts.append(
            (
                accounts.context_user()["id"],
                accounts.in_request(),
            )
        )
        return (
            StylistOutput(
                outfits=[
                    Outfit(
                        title="Look",
                        mood="Calm",
                        piece_ids=["item-1"],
                        why="Balanced.",
                    )
                ]
            ),
            type("Meta", (), {"request_id": "stylist-request"})(),
        )

    monkeypatch.setattr(phase2_api.db, "live_catalogue_batches", lambda: nullcontext([[item]]))
    monkeypatch.setattr(phase2_api.db, "profile_swipe_items", lambda _profile: [])
    monkeypatch.setattr(phase2_api, "_fitting_sizes", lambda items, _brief: items)
    monkeypatch.setattr(phase2_api, "SHARED_CORE_ENABLED", False)
    monkeypatch.setattr(phase2_api, "_core", lambda: {"rules": []})
    monkeypatch.setattr(phase2_api, "build_pool_batches", lambda *_args, **_kwargs: [item])
    monkeypatch.setattr(phase2_api, "stylist_user_content", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(phase2_api, "style_notes", lambda _brief: [])
    monkeypatch.setattr(phase2_api.db, "swipe_history", lambda _profile: {})
    monkeypatch.setattr(phase2_api, "call_task", fake_call_task)
    monkeypatch.setattr(
        phase2_api,
        "validate_outfits",
        lambda *_args, **_kwargs: (
            [
                {
                    "title": "Look",
                    "mood": "Calm",
                    "why": "Balanced.",
                    "piece_ids": [],
                    "total_gbp": 30,
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(phase2_api, "order_outfit_pieces", lambda pieces: pieces)
    monkeypatch.setattr(phase2_api.db, "save_outfit", lambda *_args: "outfit-1")
    monkeypatch.setenv("QUENYA_JOB_RUNNER", "inline")
    monkeypatch.setenv("QUENYA_OUTFIT_SYNC_WAIT_S", "3")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/outfits",
            headers={"Authorization": f"Bearer {token}"},
            json={"count": 1},
        )

    result = response.json()
    assert response.status_code == 200, response.text
    assert result["taste_version"] == 1
    assert result["outfits"][0]["id"] == "outfit-1"
    assert result["outfits"][0]["request_id"] == "stylist-request"
    assert contexts == [(owner["id"], True)]
    assert accounts.context_user() is None
    assert not accounts.in_request()


def test_legacy_endpoint_returns_job_id_when_sync_wait_expires(
    isolated_jobs_db, monkeypatch
):
    _owner, token = _user_and_profile("Timeout")
    monkeypatch.setenv("QUENYA_OUTFIT_SYNC_WAIT_S", "0")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/outfits",
            headers={"Authorization": f"Bearer {token}"},
            json={"count": 1},
        )

    assert response.status_code == 504
    assert response.json()["error"] == "Outfits are still being made"
    assert response.json()["job_id"]


def test_runner_maps_http_errors_and_resets_context(isolated_jobs_db, monkeypatch):
    owner, _token = _user_and_profile("Runner")
    job_id = jobs.enqueue("outfits", owner["id"], f"profile-{owner['id']}", {})
    job = jobs.get(job_id, owner["id"])
    user_context_token = accounts._user_context.set(None)
    request_context_token = accounts._in_request.set(False)
    captured = []
    monkeypatch.setattr(
        jobs.sentry_sdk,
        "capture_exception",
        lambda: captured.append(True),
    )

    def fail_generation(_body):
        assert accounts.context_user()["id"] == owner["id"]
        assert accounts.in_request()
        raise HTTPException(status_code=409, detail={"error": "No eligible items"})

    monkeypatch.setattr(phase2_api, "generate_outfits", fail_generation)
    try:
        jobs.run_job(job)
        stored = jobs.get(job_id, owner["id"])
        assert stored["status"] == "error"
        assert stored["error"] == {
            "status_code": 409,
            "detail": {"error": "No eligible items"},
        }
        assert accounts.context_user() is None
        assert not accounts.in_request()
        assert captured == []
    finally:
        accounts._in_request.reset(request_context_token)
        accounts._user_context.reset(user_context_token)


def test_revoked_owner_fails_job_without_setting_context(isolated_jobs_db):
    owner, _token = _user_and_profile("Revoked")
    job_id = jobs.enqueue("outfits", owner["id"], f"profile-{owner['id']}", {})
    job = jobs.get(job_id, owner["id"])
    accounts.revoke_user(owner["id"])

    jobs.run_job(job)

    stored = jobs.get(job_id, owner["id"])
    assert stored["status"] == "error"
    assert stored["error"] == {
        "status_code": 401,
        "detail": "Sign in with your invite link",
    }
    assert accounts.context_user() is None


def test_runner_maps_unexpected_errors(isolated_jobs_db, monkeypatch, caplog):
    owner, _token = _user_and_profile("Unexpected")
    job_id = jobs.enqueue("outfits", owner["id"], f"profile-{owner['id']}", {})
    job = jobs.get(job_id, owner["id"])

    def fail_generation(_body):
        raise RuntimeError("sensitive internal details")

    monkeypatch.setattr(phase2_api, "generate_outfits", fail_generation)
    captured = []
    monkeypatch.setattr(
        jobs.sentry_sdk,
        "capture_exception",
        lambda: captured.append(True),
    )

    jobs.run_job(job)

    stored = jobs.get(job_id, owner["id"])
    assert stored["status"] == "error"
    assert stored["error"] == {
        "status_code": 500,
        "detail": "Outfit generation failed",
    }
    assert "sensitive internal details" in caplog.text
    assert captured == [True]
