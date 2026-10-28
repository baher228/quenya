import logging
import sys
from types import SimpleNamespace

import pytest

from app import db


def _reset_postgres_pool(monkeypatch):
    monkeypatch.setattr(db, "_POSTGRES_POOL", None)
    monkeypatch.setattr(db, "_POSTGRES_POOL_PID", None)
    monkeypatch.setattr(db, "_POSTGRES_POOL_DSN", None)
    monkeypatch.setattr(db, "_POSTGRES_POOL_MAX_SIZE", None)
    monkeypatch.setattr(db, "_POSTGRES_CONNECTION_CLASS", None)


def _fake_pool_module(monkeypatch, pools, raw_connection):
    class FakePsycopgConnection:
        @classmethod
        def connect(cls, _conninfo="", **_kwargs):
            return raw_connection

    class FakePool:
        check_connection = object()

        def __init__(self, **kwargs):
            self.options = kwargs
            self.raw_connection = raw_connection
            self.events = []
            self.closed = False
            pools.append(self)

        def getconn(self):
            self.events.append("getconn")
            return self.raw_connection

        def putconn(self, connection):
            assert connection is self.raw_connection
            self.events.append("putconn")

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(Connection=FakePsycopgConnection),
    )
    monkeypatch.setitem(
        sys.modules,
        "psycopg_pool",
        SimpleNamespace(ConnectionPool=FakePool),
    )
    _reset_postgres_pool(monkeypatch)
    return FakePool


def test_postgres_adapter_translates_placeholders_and_ignores_conflicts():
    translated = db._PostgresConnection._translate(
        "SELECT * FROM items WHERE title LIKE '%coat%' AND id=?",
        ("item-1",),
    )
    assert translated == (
        "SELECT * FROM items WHERE title LIKE '%%coat%%' AND id=%s"
    )
    assert db._PostgresConnection._translate(
        "INSERT OR IGNORE INTO items (id) VALUES (?)",
        ("item-1",),
    ) == "INSERT INTO items (id) VALUES (%s) ON CONFLICT DO NOTHING"
    assert db._PostgresConnection._translate(
        "INSERT INTO items (id) VALUES (:id)",
        {"id": "item-1"},
    ) == "INSERT INTO items (id) VALUES (%(id)s)"


def test_postgres_rows_support_index_and_key_access():
    row = db._HybridRow(["id", "title"], (7, "Wax jacket"))

    assert row[0] == row["id"] == 7
    assert row[1] == row["title"] == "Wax jacket"
    assert dict(row) == {"id": 7, "title": "Wax jacket"}


def test_postgres_connect_disables_prepared_statements_and_sets_search_path(
    monkeypatch,
):
    calls = []

    class FakeConnection:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    def connect(url, **kwargs):
        calls.append((url, kwargs))
        return FakeConnection()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("QUENYA_DB_POOL", "0")

    connection = db.connect()

    assert calls[0] == (
        "postgresql://local/test",
        {
            "prepare_threshold": None,
            "row_factory": db._postgres_row_factory,
        },
    )
    assert len(calls) == 1
    connection.execute("SELECT 1")
    connection.execute("SELECT 2")
    connection.commit()
    connection.execute("SELECT 3")
    assert calls[1:] == [
        ("SET LOCAL search_path TO quenya, extensions", None),
        ("SELECT 1", None),
        ("SELECT 2", None),
        ("SET LOCAL search_path TO quenya, extensions", None),
        ("SELECT 3", None),
    ]
    connection.close()


def test_postgres_init_db_takes_advisory_lock_as_first_statement(monkeypatch):
    statements = []
    commits = []

    class FakeConnection:
        def execute(self, sql, params=None):
            statements.append((sql, params))

        def commit(self):
            commits.append(len(statements))

        def rollback(self):
            pass

        def close(self):
            pass

    fake_connection = FakeConnection()
    monkeypatch.setattr(
        db,
        "connect_postgres",
        lambda url, **kwargs: fake_connection,
    )
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://test/database")
    monkeypatch.setenv("QUENYA_DB_POOL", "0")
    monkeypatch.delenv("QUENYA_SKIP_DB_INIT", raising=False)
    monkeypatch.setattr(db, "_INITIALIZED_DATABASES", set())

    db.init_db()

    assert statements[0] == (
        "SELECT pg_advisory_xact_lock(%s)",
        (db._POSTGRES_INIT_ADVISORY_LOCK_KEY,),
    )
    assert statements[1] == ("SET LOCAL search_path TO quenya, extensions", None)
    assert commits == [len(statements)]


def test_postgres_connect_redacts_password_from_malformed_dsn_error(monkeypatch):
    import psycopg

    dsn = "postgresql://importer:test-password@db.example.invalid/postgres"

    def connect(url, **kwargs):
        raise psycopg.ProgrammingError(
            f'missing "=" after "{url}" in connection info string'
        )

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("QUENYA_DATABASE_URL", dsn)
    monkeypatch.setenv("QUENYA_DB_POOL", "0")

    try:
        db.connect()
    except psycopg.ProgrammingError as exc:
        assert "test-password" not in str(exc)
        assert "importer" not in str(exc)
        assert "[REDACTED]@" in str(exc)
    else:
        raise AssertionError("expected a malformed DSN error")


def test_postgres_pool_uses_configured_settings_and_returns_connection(monkeypatch):
    events = []
    pools = []

    class FakeConnection:
        def execute(self, sql, params=None):
            events.append(("execute", sql, params))

        def commit(self):
            events.append(("commit",))

        def rollback(self):
            events.append(("rollback",))

        def close(self):
            events.append(("close",))

    raw_connection = FakeConnection()
    fake_pool = _fake_pool_module(monkeypatch, pools, raw_connection)
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("QUENYA_DB_POOL_MAX", "4")
    monkeypatch.delenv("QUENYA_DB_POOL", raising=False)

    connection = db.connect()
    connection.execute("SELECT 1")
    connection.close()
    second = db.connect()
    second.execute("SELECT 2")
    second.close()

    assert len(pools) == 1
    pool = pools[0]
    assert pool.options["min_size"] == 1
    assert pool.options["max_size"] == 4
    assert pool.options["max_idle"] == 300
    assert pool.options["max_lifetime"] == 1800
    assert pool.options["timeout"] == 15
    assert pool.options["check"] is fake_pool.check_connection
    assert pool.options["open"] is True
    assert pool.options["kwargs"] == {
        "prepare_threshold": None,
        "row_factory": db._postgres_row_factory,
    }
    assert pool.events == ["getconn", "putconn", "getconn", "putconn"]
    assert events.count(("rollback",)) == 2
    assert ("close",) not in events


def test_postgres_pool_rolls_back_on_error_before_returning_connection(monkeypatch):
    events = []
    pools = []

    class FakeConnection:
        def execute(self, _sql, _params=None):
            events.append("execute")

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    raw_connection = FakeConnection()
    _fake_pool_module(monkeypatch, pools, raw_connection)
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://local/test")
    monkeypatch.delenv("QUENYA_DB_POOL", raising=False)

    with pytest.raises(RuntimeError, match="forced failure"):
        with db.db_connection() as connection:
            connection.execute("SELECT 1")
            raise RuntimeError("forced failure")

    assert events.count("rollback") == 1
    assert events[-1] == "rollback"
    assert pools[0].events == ["getconn", "putconn"]


def test_postgres_pool_is_recreated_after_pid_change(monkeypatch):
    pools = []

    class FakeConnection:
        def execute(self, _sql, _params=None):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    raw_connection = FakeConnection()
    _fake_pool_module(monkeypatch, pools, raw_connection)
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://local/test")
    monkeypatch.delenv("QUENYA_DB_POOL", raising=False)
    pid = [1234]
    monkeypatch.setattr(db.os, "getpid", lambda: pid[0])

    db.connect().close()
    pid[0] = 5678
    db.connect().close()

    assert len(pools) == 2
    assert pools[0].closed is False


def test_postgres_pool_can_be_disabled(monkeypatch):
    events = []
    pools = []

    class FakeConnection:
        def execute(self, _sql, _params=None):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            events.append("close")

    raw_connection = FakeConnection()
    _fake_pool_module(monkeypatch, pools, raw_connection)
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("QUENYA_DB_POOL", "0")
    connects = []
    monkeypatch.setattr(
        db,
        "connect_postgres",
        lambda dsn, **kwargs: connects.append((dsn, kwargs)) or raw_connection,
    )

    connection = db.connect()
    connection.close()

    assert pools == []
    assert connects == [
        (
            "postgresql://local/test",
            {
                "prepare_threshold": None,
                "row_factory": db._postgres_row_factory,
            },
        )
    ]
    assert events == ["close"]


def test_postgres_pool_connection_failure_redacts_dsn_password(monkeypatch, caplog):
    dsn = "postgresql://importer:test-password@db.example.invalid/postgres"

    class FakeProgrammingError(Exception):
        pass

    class FakePsycopgConnection:
        @classmethod
        def connect(cls, conninfo="", **_kwargs):
            raise FakeProgrammingError(f'missing "=" after "{conninfo}"')

    pools = []

    class FakePool:
        check_connection = object()

        def __init__(self, **kwargs):
            pools.append(kwargs)
            try:
                kwargs["connection_class"].connect(kwargs["conninfo"])
            except Exception as exc:
                logging.getLogger("psycopg.pool").error(
                    "connection failed: %s",
                    exc,
                )
                raise

    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(
            Connection=FakePsycopgConnection,
            ProgrammingError=FakeProgrammingError,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "psycopg_pool",
        SimpleNamespace(ConnectionPool=FakePool),
    )
    _reset_postgres_pool(monkeypatch)
    monkeypatch.setenv("QUENYA_DATABASE_URL", dsn)
    monkeypatch.delenv("QUENYA_DB_POOL", raising=False)
    caplog.set_level(logging.ERROR, logger="psycopg.pool")

    with pytest.raises(FakeProgrammingError) as exc_info:
        db.connect()

    assert "test-password" not in str(exc_info.value)
    assert "importer" not in str(exc_info.value)
    assert "[REDACTED]@" in str(exc_info.value)
    assert "test-password" not in caplog.text
    assert "importer" not in caplog.text
    assert "[REDACTED]@" in caplog.text


def test_supabase_import_connect_redacts_password_from_dsn_error(monkeypatch):
    import psycopg

    from app import supabase_import

    dsn = "postgresql://importer:test-password@db.example.invalid/postgres"

    def connect(url, **kwargs):
        raise psycopg.ProgrammingError(
            f'missing "=" after "{url}" in connection info string'
        )

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("SUPABASE_DB_URL", dsn)

    try:
        supabase_import._connect()
    except psycopg.ProgrammingError as exc:
        assert "test-password" not in str(exc)
        assert "importer" not in str(exc)
        assert "[REDACTED]@" in str(exc)
    else:
        raise AssertionError("expected a malformed DSN error")
