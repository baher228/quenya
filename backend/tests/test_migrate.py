from __future__ import annotations

import pytest
from psycopg.errors import UndefinedTable

from app import db, migrate


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def acquire_advisory_xact_lock(self, key):
        self.state["statements"].append(("advisory_lock", (key,)))
        if self.state.get("lock_timeout"):
            from psycopg.errors import LockNotAvailable

            raise LockNotAvailable("lock timeout")

    def execute(self, statement, params=()):
        self.state["statements"].append((statement, params))
        if "to_regclass('schema_migrations')" in statement:
            value = "schema_migrations" if self.state.get("tracking") else None
            return _Cursor((value,))
        if statement == "SELECT version FROM schema_migrations":
            if not self.state.get("tracking"):
                raise UndefinedTable("schema_migrations does not exist")
            return _Cursor(rows=[(version,) for version in self.state["applied"]])
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in statement:
            self.state["tracking"] = True
        if "SELECT 1 FROM schema_migrations WHERE version = ?" in statement:
            return _Cursor((1,) if params[0] in self.state["applied"] else None)
        if "INSERT INTO schema_migrations" in statement:
            self.state["applied"].add(params[0])
        if "pg_terminate_backend" in statement:
            self.state["termination_queries"].append(params)
            self.state["lock_timeout"] = False
            return _Cursor((True,))
        if "pg_stat_activity" in statement:
            self.state["blocker_queries"].append(params)
            return _Cursor(rows=self.state.get("blockers", []))
        return _Cursor()

    def commit(self):
        self.state["commits"] += 1

    def rollback(self):
        self.state["rollbacks"] += 1

    def close(self):
        pass


def _state(**overrides):
    state = {
        "applied": set(),
        "blocker_queries": [],
        "commits": 0,
        "lock_timeout": False,
        "rollbacks": 0,
        "statements": [],
        "termination_queries": [],
        "tracking": False,
    }
    state.update(overrides)
    return state


def _configure(monkeypatch, state):
    connections = []

    def connection_factory():
        connection = _Connection(state)
        connections.append(connection)
        return connection

    monkeypatch.setattr(db, "db_connection", connection_factory)
    return connections


def test_run_applies_pending_migrations_in_order_and_is_idempotent(monkeypatch):
    state = _state()
    connections = _configure(monkeypatch, state)
    applied_in_order = []

    def first(connection):
        applied_in_order.append("0001_first")
        connection.execute("SELECT first migration")

    def second(connection):
        applied_in_order.append("0002_second")
        connection.execute("SELECT second migration")

    monkeypatch.setattr(
        migrate,
        "MIGRATIONS",
        [("0001_first", first), ("0002_second", second)],
    )
    monkeypatch.setattr(migrate, "LATEST_VERSION", "0002_second")

    assert migrate.run() == ["0001_first", "0002_second"]
    assert state["applied"] == {"0001_first", "0002_second"}
    assert applied_in_order == ["0001_first", "0002_second"]
    assert len(connections) == 3
    assert state["commits"] == 3
    migration_statements = [
        statement
        for statement, _params in state["statements"]
        if statement.startswith("SET LOCAL")
    ]
    assert migration_statements == [
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL statement_timeout = '120s'",
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL statement_timeout = '120s'",
    ]

    assert migrate.run() == []
    assert applied_in_order == ["0001_first", "0002_second"]
    assert len(connections) == 4


def test_cli_check_exit_codes(monkeypatch, capsys):
    state = _state()
    _configure(monkeypatch, state)
    monkeypatch.setattr(
        migrate,
        "MIGRATIONS",
        [("0001_first", lambda _connection: None)],
    )
    monkeypatch.setattr(migrate, "LATEST_VERSION", "0001_first")

    assert migrate.main(["--check"]) == 1
    assert "Pending migrations: 0001_first" in capsys.readouterr().out

    state["tracking"] = True
    state["applied"].add("0001_first")
    assert migrate.main(["--check"]) == 0
    assert "up to date at 0001_first" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("tracking", "applied", "expected_version", "expected_ok"),
    [
        (False, set(), None, False),
        (True, set(), None, False),
        (True, {"0001_baseline"}, "0001_baseline", False),
        (
            True,
            {"0001_baseline", "0002_jobs"},
            "0002_jobs",
            False,
        ),
        (
            True,
            {"0001_baseline", "0002_jobs", "0003_rate_limits"},
            "0003_rate_limits",
            False,
        ),
        (
            True,
            {
                "0001_baseline",
                "0002_jobs",
                "0003_rate_limits",
                "0004_vinted_privacy",
            },
            "0004_vinted_privacy",
            False,
        ),
        (
            True,
            {
                "0001_baseline",
                "0002_jobs",
                "0003_rate_limits",
                "0004_vinted_privacy",
                "0005_cost_indexes",
            },
            "0005_cost_indexes",
            False,
        ),
        (
            True,
            {
                "0001_baseline",
                "0002_jobs",
                "0003_rate_limits",
                "0004_vinted_privacy",
                "0005_cost_indexes",
                "0006_preferred_brands",
            },
            "0006_preferred_brands",
            True,
        ),
    ],
)
def test_init_db_without_auto_migrate_only_checks_schema_versions(
    monkeypatch,
    caplog,
    tracking,
    applied,
    expected_version,
    expected_ok,
):
    state = _state(tracking=tracking, applied=set(applied))
    _configure(monkeypatch, state)
    monkeypatch.setenv("QUENYA_DATABASE_URL", "postgresql://test/database")
    monkeypatch.setenv("QUENYA_AUTO_MIGRATE", "0")
    monkeypatch.delenv("QUENYA_SKIP_DB_INIT", raising=False)
    monkeypatch.setattr(db, "_INITIALIZED_DATABASES", set())
    monkeypatch.setattr(
        db,
        "_POSTGRES_SCHEMA_STATUS",
        {"version": None, "latest": None, "ok": False},
    )
    monkeypatch.setattr(
        migrate,
        "run",
        lambda **_kwargs: pytest.fail("auto migrations must be disabled"),
    )

    db.init_db()

    assert "postgres:postgresql://test/database" in db._INITIALIZED_DATABASES
    assert db.schema_migration_status() == {
        "version": expected_version,
        "latest": migrate.LATEST_VERSION,
        "ok": expected_ok,
    }
    assert len(state["statements"]) == 1
    assert state["statements"][0][0] == "SELECT version FROM schema_migrations"
    assert not any(
        statement.lstrip().upper().startswith(("CREATE", "ALTER", "UPDATE"))
        for statement, _params in state["statements"]
    )
    if not expected_ok:
        assert "Postgres schema migrations are pending" in caplog.text


def test_terminate_flag_terminates_only_stale_idle_sessions_then_retries(
    monkeypatch,
):
    state = _state(
        blockers=[
            {
                "pid": 123,
                "usename": "quenya",
                "application_name": "old-worker",
                "state": "idle in transaction",
                "xact_age": "0:06:00",
                "query": "SELECT 1",
            }
        ],
        lock_timeout=True,
    )
    _configure(monkeypatch, state)
    monkeypatch.setattr(
        migrate,
        "MIGRATIONS",
        [("0001_first", lambda connection: connection.execute("SELECT migration"))],
    )
    monkeypatch.setattr(migrate, "LATEST_VERSION", "0001_first")

    assert migrate.main(["--terminate-idle-in-transaction"]) == 0
    assert state["applied"] == {"0001_first"}
    assert state["blocker_queries"] == [(60,), (300,)]
    assert state["termination_queries"] == [(123,)]


def test_jobs_migration_creates_additive_table_and_indexes():
    statements = []

    class Connection:
        def execute(self, statement, _params=()):
            statements.append(statement)

    migrate._jobs(Connection())

    assert statements[0].startswith("CREATE TABLE IF NOT EXISTS jobs")
    assert "payload_json TEXT NOT NULL" in statements[0]
    assert "attempts INTEGER NOT NULL DEFAULT 0" in statements[0]
    assert "idx_jobs_status_created" in statements[1]
    assert "idx_jobs_owner_created" in statements[2]


def test_jobs_migration_creates_table_and_indexes():
    statements = []

    class Connection:
        def execute(self, statement, _params=()):
            statements.append(statement)

    migrate._jobs(Connection())

    assert "CREATE TABLE IF NOT EXISTS jobs" in statements[0]
    assert "idx_jobs_status_created" in statements[1]
    assert "idx_jobs_owner_created" in statements[2]
