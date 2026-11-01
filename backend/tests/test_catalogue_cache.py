from threading import Event, Thread

import pytest

from app import db


@pytest.fixture(params=[("live_catalogue", "_load_live_catalogue"),
                        ("deck_catalogue", "_load_deck_catalogue")])
def cache(request, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "monotonic", lambda: clock[0])
    name, loader = request.param
    return getattr(db, name), loader, clock


def _refresh_threads(monkeypatch):
    threads = []

    def create(*args, **kwargs):
        thread = Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(db, "Thread", create)
    return threads


def _join(threads):
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_concurrent_cold_calls_load_once_and_copy_results(cache, monkeypatch):
    get, loader_name, _clock = cache
    started, release = Event(), Event()
    calls = []
    results = []
    items = [{"id": "shared"}]

    def load():
        calls.append(1)
        started.set()
        assert release.wait(timeout=5)
        return items

    monkeypatch.setattr(db, loader_name, load)
    threads = [Thread(target=lambda: results.append(get())) for _ in range(8)]
    for thread in threads:
        thread.start()
    try:
        assert started.wait(timeout=5)
    finally:
        release.set()
        _join(threads)
    assert calls == [1]
    assert results == [items] * 8
    assert all(rows[0] is not items[0] for rows in results)
    assert len({id(rows[0]) for rows in results}) == 8


def test_concurrent_expired_calls_return_stale_and_start_one_refresh(cache, monkeypatch):
    get, loader_name, clock = cache
    started, release = Event(), Event()
    calls = []

    def load():
        calls.append(1)
        if len(calls) == 1:
            return [{"id": "old"}]
        started.set()
        assert release.wait(timeout=5)
        return [{"id": "new"}]

    monkeypatch.setattr(db, loader_name, load)
    refreshes = _refresh_threads(monkeypatch)
    assert get() == [{"id": "old"}]
    clock[0] += db.QUENYA_CATALOGUE_TTL_S
    results = []
    callers = [Thread(target=lambda: results.append(get())) for _ in range(8)]
    try:
        for thread in callers:
            thread.start()
        _join(callers)
        assert started.wait(timeout=5)
        assert results == [[{"id": "old"}]] * 8
        assert len(refreshes) == 1
    finally:
        release.set()
        _join(refreshes)
    assert get() == [{"id": "new"}]
    assert calls == [1, 1]


def test_cold_load_failure_can_retry(cache, monkeypatch):
    get, loader_name, _clock = cache

    def fail():
        raise RuntimeError("Synthetic loader failure")

    monkeypatch.setattr(db, loader_name, fail)
    with pytest.raises(RuntimeError, match="Synthetic loader failure"):
        get()
    monkeypatch.setattr(db, loader_name, lambda: [{"id": "recovered"}])
    assert get() == [{"id": "recovered"}]


def test_refresh_failure_keeps_stale_and_retries(cache, monkeypatch, caplog):
    get, loader_name, clock = cache
    items = [{"id": "old"}]
    fail = [False]

    def load():
        if fail[0]:
            raise RuntimeError("Synthetic refresh failure")
        return items

    monkeypatch.setattr(db, loader_name, load)
    refreshes = _refresh_threads(monkeypatch)
    assert get() == items
    clock[0] += db.QUENYA_CATALOGUE_TTL_S
    fail[0] = True
    assert get() == items
    _join(refreshes)
    assert "Synthetic refresh failure" in caplog.text
    fail[0] = False
    items = [{"id": "new"}]
    assert get() == [{"id": "old"}]
    _join(refreshes)
    assert get() == items


def test_thread_start_failure_does_not_block_later_refresh(cache, monkeypatch):
    get, loader_name, clock = cache
    items = [{"id": "old"}]
    monkeypatch.setattr(db, loader_name, lambda: items)
    assert get() == items
    clock[0] += db.QUENYA_CATALOGUE_TTL_S

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("Synthetic thread failure")

    monkeypatch.setattr(db, "Thread", BrokenThread)
    assert get() == items
    items = [{"id": "new"}]
    refreshes = _refresh_threads(monkeypatch)
    assert get() == [{"id": "old"}]
    _join(refreshes)
    assert get() == items


def test_invalidated_inflight_refresh_cannot_replace_new_snapshot(cache, monkeypatch):
    get, loader_name, clock = cache
    started, release = Event(), Event()
    calls = []

    def load():
        calls.append(1)
        if len(calls) == 1:
            return [{"id": "old"}]
        if len(calls) == 2:
            started.set()
            assert release.wait(timeout=5)
            return [{"id": "obsolete"}]
        return [{"id": "current"}]

    monkeypatch.setattr(db, loader_name, load)
    refreshes = _refresh_threads(monkeypatch)
    assert get() == [{"id": "old"}]
    clock[0] += db.QUENYA_CATALOGUE_TTL_S
    assert get() == [{"id": "old"}]
    try:
        assert started.wait(timeout=5)
        db.invalidate_live_catalogue()
        assert get() == [{"id": "current"}]
    finally:
        release.set()
        _join(refreshes)
    assert get() == [{"id": "current"}]


@pytest.mark.parametrize("fail_deck", [False, True])
def test_startup_warms_only_bounded_catalogues(monkeypatch, fail_deck):
    from app import embedding_ranker, main

    calls = []

    def warm_deck():
        calls.append("deck")
        if fail_deck:
            raise RuntimeError("Synthetic warmup failure")

    def fail_full():
        calls.append("full")
        raise AssertionError("Startup must not load the full catalogue")

    monkeypatch.setattr(db, "deck_catalogue", warm_deck)
    monkeypatch.setattr(db, "live_catalogue", fail_full)
    monkeypatch.setattr(embedding_ranker, "catalogue_sample", lambda: calls.append("sample"))
    main._warm_caches()
    assert calls == ["deck", "sample"]
    assert db._LIVE_CATALOGUE.items is None
