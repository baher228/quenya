from app import main


def test_health_reports_llm_provider_readiness(monkeypatch):
    monkeypatch.setattr(main.db, "total_counts", lambda: (12, 8))
    monkeypatch.setattr(
        main.jobs,
        "stats",
        lambda: {"queued": 2, "running": 1, "oldest_queued_s": 3.5},
    )
    monkeypatch.setattr(
        main.db,
        "schema_migration_status",
        lambda: {"version": "0001_baseline", "latest": "0001_baseline", "ok": True},
    )
    monkeypatch.setattr(main, "QUENYA_LLM_PROVIDER", "cursor")
    monkeypatch.setattr(main, "CURSOR_API_KEY", "configured")

    response = main.health()

    assert response["ok"] is True
    assert response["llm_provider"] == "cursor"
    assert response["llm_ready"] is True
    assert {"llm_provider", "llm_ready"} <= response.keys()
    assert response["items_total"] == 12
    assert response["items_tagged"] == 8
    assert response["jobs"] == {
        "queued": 2,
        "running": 1,
        "oldest_queued_s": 3.5,
    }
    assert response["schema"] == {
        "version": "0001_baseline",
        "latest": "0001_baseline",
        "ok": True,
    }
    assert any(route.path == "/health" for route in main.app.routes)
