import logging
from threading import Barrier, Event, Thread

import numpy as np
import pytest

from app import db, embedding_ranker


def _track_refresh_threads(monkeypatch, module):
    threads = []

    def tracking_thread(*args, **kwargs):
        thread = Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(module, "Thread", tracking_thread)
    return threads


def _join_threads(threads):
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)


@pytest.fixture
def sample_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(embedding_ranker, "monotonic", lambda: clock[0])
    monkeypatch.setattr(embedding_ranker, "_CATALOGUE_SAMPLE_TTL_S", 10)
    with embedding_ranker._catalogue_cache_lock:
        embedding_ranker._catalogue_cache = None
        embedding_ranker._catalogue_refresh_in_flight = False
    return clock


@pytest.fixture
def live_catalogue_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(db, "monotonic", lambda: clock[0])
    monkeypatch.setattr(db, "QUENYA_CATALOGUE_TTL_S", 10)
    monkeypatch.setattr(db, "init_db", lambda: None)
    db.invalidate_live_catalogue()
    return clock


def test_catalogue_sample_cold_load_blocks_for_loader(monkeypatch, sample_clock):
    sample = np.asarray([[1.0, 2.0]], dtype=np.float32)
    loader_started = Event()
    release_loader = Event()
    result = []
    returned = Event()

    def loader():
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return sample

    monkeypatch.setattr(embedding_ranker, "_load_catalogue_sample", loader)
    caller = Thread(
        target=lambda: (
            result.append(embedding_ranker.catalogue_sample()),
            returned.set(),
        )
    )
    caller.start()

    try:
        assert loader_started.wait(timeout=5)
        assert not returned.is_set()
    finally:
        release_loader.set()
        caller.join(timeout=5)

    assert not caller.is_alive()
    assert returned.is_set()
    assert result[0] is sample


def test_catalogue_sample_returns_stale_then_uses_background_refresh(
    monkeypatch, sample_clock
):
    old = np.asarray([[1.0]], dtype=np.float32)
    new = np.asarray([[2.0]], dtype=np.float32)
    calls = 0
    loader_started = Event()
    release_loader = Event()
    returned = Event()
    result = []
    refresh_threads = _track_refresh_threads(monkeypatch, embedding_ranker)

    def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return new

    monkeypatch.setattr(embedding_ranker, "_load_catalogue_sample", loader)
    assert embedding_ranker.catalogue_sample() is old
    sample_clock[0] += embedding_ranker._CATALOGUE_SAMPLE_TTL_S

    caller = Thread(
        target=lambda: (
            result.append(embedding_ranker.catalogue_sample()),
            returned.set(),
        )
    )
    caller.start()
    try:
        assert loader_started.wait(timeout=5)
        assert returned.wait(timeout=1)
        assert result[0] is old
    finally:
        release_loader.set()
        caller.join(timeout=5)
        _join_threads(refresh_threads)

    assert not caller.is_alive()
    assert embedding_ranker.catalogue_sample() is new
    assert calls == 2


def test_catalogue_sample_concurrent_expiry_starts_one_refresh(
    monkeypatch, sample_clock
):
    old = np.asarray([[1.0]], dtype=np.float32)
    new = np.asarray([[2.0]], dtype=np.float32)
    calls = 0
    loader_started = Event()
    release_loader = Event()
    barrier = Barrier(3)
    results = []
    refresh_threads = _track_refresh_threads(monkeypatch, embedding_ranker)

    def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return new

    monkeypatch.setattr(embedding_ranker, "_load_catalogue_sample", loader)
    assert embedding_ranker.catalogue_sample() is old
    sample_clock[0] += embedding_ranker._CATALOGUE_SAMPLE_TTL_S

    def request_sample():
        barrier.wait(timeout=2)
        results.append(embedding_ranker.catalogue_sample())

    callers = [Thread(target=request_sample) for _ in range(2)]
    for caller in callers:
        caller.start()
    try:
        barrier.wait(timeout=5)
        assert loader_started.wait(timeout=5)
        for caller in callers:
            caller.join(timeout=5)
        assert len(results) == 2
        assert all(result is old for result in results)
    finally:
        release_loader.set()
        for caller in callers:
            caller.join(timeout=5)
        _join_threads(refresh_threads)

    assert calls == 2
    assert embedding_ranker.catalogue_sample() is new


def test_catalogue_sample_failed_refresh_keeps_stale_and_retries(
    monkeypatch, sample_clock, caplog
):
    old = np.asarray([[1.0]], dtype=np.float32)
    new = np.asarray([[2.0]], dtype=np.float32)
    calls = 0
    refresh_threads = _track_refresh_threads(monkeypatch, embedding_ranker)

    def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        if calls == 2:
            raise RuntimeError("refresh failed")
        return new

    caplog.set_level(logging.ERROR, logger=embedding_ranker.__name__)
    monkeypatch.setattr(embedding_ranker, "_load_catalogue_sample", loader)
    assert embedding_ranker.catalogue_sample() is old
    sample_clock[0] += embedding_ranker._CATALOGUE_SAMPLE_TTL_S

    assert embedding_ranker.catalogue_sample() is old
    _join_threads(refresh_threads[:1])
    assert embedding_ranker._catalogue_cache[1] is old
    assert "Failed to refresh embedding catalogue sample" in caplog.text

    assert embedding_ranker.catalogue_sample() is old
    _join_threads(refresh_threads[1:])
    assert embedding_ranker.catalogue_sample() is new
    assert calls == 3


def test_live_catalogue_returns_stale_while_refresh_is_blocked(
    monkeypatch, live_catalogue_clock
):
    old = [{"id": "old"}]
    new = [{"id": "new"}]
    calls = 0
    loader_started = Event()
    release_loader = Event()
    returned = Event()
    result = []
    refresh_threads = _track_refresh_threads(monkeypatch, db)

    def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return new

    monkeypatch.setattr(db, "_load_live_catalogue", loader)
    first = db.live_catalogue()
    assert first[0] == old[0]
    assert first[0] is not old[0]
    live_catalogue_clock[0] += db.QUENYA_CATALOGUE_TTL_S

    caller = Thread(
        target=lambda: (
            result.append(db.live_catalogue()),
            returned.set(),
        )
    )
    caller.start()
    try:
        assert loader_started.wait(timeout=5)
        assert returned.wait(timeout=1)
        assert result[0][0] == old[0]
    finally:
        release_loader.set()
        caller.join(timeout=5)
        _join_threads(refresh_threads)

    assert not caller.is_alive()
    assert db.live_catalogue() == new
    assert calls == 2


def test_live_catalogue_invalidation_forces_blocking_reload(
    monkeypatch, live_catalogue_clock
):
    old = [{"id": "old"}]
    new = [{"id": "new"}]
    calls = 0
    loader_started = Event()
    release_loader = Event()
    returned = Event()
    result = []

    def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return new

    monkeypatch.setattr(db, "_load_live_catalogue", loader)
    assert db.live_catalogue() == old
    db.invalidate_live_catalogue()

    caller = Thread(
        target=lambda: (
            result.append(db.live_catalogue()),
            returned.set(),
        )
    )
    caller.start()
    try:
        assert loader_started.wait(timeout=5)
        assert not returned.wait(timeout=0.2)
    finally:
        release_loader.set()
        caller.join(timeout=5)

    assert not caller.is_alive()
    assert returned.is_set()
    assert result == [new]
    assert calls == 2
