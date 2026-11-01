import logging

import pytest
from psycopg.errors import LockNotAvailable

from app import db


class _FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, state=None, *, existing_columns=True):
        self.state = state or {}
        self.existing_columns = existing_columns
        self.statements = []
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False

    def acquire_advisory_xact_lock(self, key):
        self.statements.append(("advisory_lock", key))

    def execute(self, statement, params=()):
        self.statements.append((statement, params))
        if statement == "CREATE SCHEMA IF NOT EXISTS quenya":
            if self.state.get("always_fail"):
                self.state["lock_failures"] += 1
                raise LockNotAvailable("lock timeout")
            if self.state.get("failures_remaining", 0):
                self.state["failures_remaining"] -= 1
                self.state["lock_failures"] += 1
                raise LockNotAvailable("lock timeout")
        if "information_schema.columns" in statement:
            row = (1,) if self.existing_columns else None
            return _FakeCursor(row)
        if "to_regclass('items')" in statement:
            return _FakeCursor((self.state.get("items_exists", True),))
        return _FakeCursor()

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.close_count += 1


def test_pg_add_column_if_missing_skips_existing_column():
    connection = _FakeConnection(existing_columns=True)

    db._pg_add_column_if_missing(connection, "items", "image_preview", "TEXT")

    assert len(connection.statements) == 1
    assert "information_schema.columns" in connection.statements[0][0]
    assert connection.statements[0][1] == ("items", "image_preview")


def test_pg_add_column_if_missing_alters_missing_column():
    connection = _FakeConnection(existing_columns=False)

    db._pg_add_column_if_missing(connection, "items", "image_preview", "TEXT")

    assert connection.statements[-1][0] == (
        "ALTER TABLE items ADD COLUMN IF NOT EXISTS image_preview TEXT"
    )


def _configure_init_db(monkeypatch, state, sleeps):
    connections = []
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://test/database")
    monkeypatch.delenv("QUENYA_SKIP_DB_INIT", raising=False)
    monkeypatch.setattr(db, "_INITIALIZED_DATABASES", set())
    monkeypatch.setattr(db, "POSTGRES_SCHEMA", "")
    monkeypatch.setattr(db, "sleep", sleeps.append)

    def connection_factory():
        connection = _FakeConnection(state)
        connections.append(connection)
        return connection

    monkeypatch.setattr(db, "db_connection", connection_factory)
    return connections


def test_postgres_init_retries_lock_not_available_then_succeeds(
    monkeypatch, caplog
):
    state = {"failures_remaining": 2, "lock_failures": 0, "items_exists": True}
    sleeps = []
    connections = _configure_init_db(monkeypatch, state, sleeps)

    with caplog.at_level(logging.WARNING):
        db.init_db()

    assert state["lock_failures"] == 2
    assert sleeps == [2, 4]
    assert len(connections) == 3
    assert [connection.rollback_count for connection in connections] == [1, 1, 0]
    assert connections[-1].commit_count == 1
    assert connections[-1].statements[:2] == [
        ("advisory_lock", db._POSTGRES_INIT_ADVISORY_LOCK_KEY),
        ("SET LOCAL lock_timeout = '5s'", ()),
    ]
    assert "CREATE SCHEMA IF NOT EXISTS quenya" in caplog.text
    assert "retrying in 2s" in caplog.text
    assert "postgres:postgresql://test/database" in db._INITIALIZED_DATABASES


def test_postgres_init_persistent_lock_with_items_table_marks_initialized(
    monkeypatch, caplog
):
    state = {"always_fail": True, "lock_failures": 0, "items_exists": True}
    sleeps = []
    connections = _configure_init_db(monkeypatch, state, sleeps)

    with caplog.at_level(logging.ERROR):
        db.init_db()

    assert state["lock_failures"] == 4
    assert sleeps == [2, 4, 8]
    assert len(connections) == 5
    assert all(connection.rollback_count == 1 for connection in connections[:4])
    assert connections[-1].commit_count == 1
    assert "items table exists" in caplog.text
    assert "postgres:postgresql://test/database" in db._INITIALIZED_DATABASES


def test_postgres_init_persistent_lock_without_items_table_raises(monkeypatch):
    state = {"always_fail": True, "lock_failures": 0, "items_exists": False}
    sleeps = []
    connections = _configure_init_db(monkeypatch, state, sleeps)

    with pytest.raises(LockNotAvailable):
        db.init_db()

    assert state["lock_failures"] == 4
    assert sleeps == [2, 4, 8]
    assert len(connections) == 5
    assert "postgres:postgresql://test/database" not in db._INITIALIZED_DATABASES
