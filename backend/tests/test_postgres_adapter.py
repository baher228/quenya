import sys
from types import SimpleNamespace

from app import db


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
        ("SET LOCAL search_path TO quenya", None),
        ("SELECT 1", None),
        ("SELECT 2", None),
        ("SET LOCAL search_path TO quenya", None),
        ("SELECT 3", None),
    ]
    connection.close()
