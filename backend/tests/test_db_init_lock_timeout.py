import logging

from psycopg.errors import LockNotAvailable

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

    def acquire_advisory_xact_lock(self, _key):
        if self.state["lock_timeout"]:
            raise LockNotAvailable("lock timeout")

    def execute(self, statement, params=()):
        self.state["statements"].append((statement, params))
        if "to_regclass('schema_migrations')" in statement:
            return _Cursor(("schema_migrations",) if self.state["tracking"] else (None,))
        if statement == "SELECT version FROM schema_migrations":
            return _Cursor(rows=[(version,) for version in self.state["applied"]])
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in statement:
            self.state["tracking"] = True
        if "SELECT 1 FROM schema_migrations WHERE version = ?" in statement:
            return _Cursor((1,) if params[0] in self.state["applied"] else None)
        if "INSERT INTO schema_migrations" in statement:
            self.state["applied"].add(params[0])
        if "pg_stat_activity" in statement:
            self.state["blocker_queries"].append(params)
            return _Cursor(rows=self.state["blockers"])
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_migration_lock_timeout_logs_blockers_and_cli_exits_nonzero(
    monkeypatch, caplog
):
    state = {
        "applied": set(),
        "blockers": [
            {
                "pid": 123,
                "usename": "quenya",
                "application_name": "orphaned-worker",
                "state": "idle in transaction",
                "xact_age": "0:02:00",
                "query": "SELECT * FROM items",
            }
        ],
        "blocker_queries": [],
        "lock_timeout": True,
        "statements": [],
        "termination_queries": [],
        "tracking": False,
    }
    connections = []

    def connection_factory():
        connection = _Connection(state)
        connections.append(connection)
        return connection

    monkeypatch.setattr(db, "db_connection", connection_factory)
    monkeypatch.setattr(
        migrate,
        "MIGRATIONS",
        [("0001_test", lambda _connection: None)],
    )
    monkeypatch.setattr(migrate, "LATEST_VERSION", "0001_test")

    with caplog.at_level(logging.ERROR):
        exit_code = migrate.main([])

    assert exit_code == 1
    assert state["blocker_queries"] == [(60,)]
    assert "Migration lock blocker" in caplog.text
    assert "application_name=orphaned-worker" in caplog.text
    assert "SELECT * FROM items" in caplog.text
    assert state["applied"] == set()
    assert state["termination_queries"] == []
    assert len(connections) == 3
