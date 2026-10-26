import os

import psycopg
import pytest

from app import db


@pytest.fixture(autouse=True)
def fresh_postgres_schema(monkeypatch):
    postgres_url = os.getenv("QUENYA_TEST_PG_URL")
    if not postgres_url:
        return
    monkeypatch.setenv("QUENYA_DATABASE_URL", postgres_url)
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS quenya CASCADE")
        connection.execute("CREATE SCHEMA quenya")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()


@pytest.fixture(autouse=True)
def clear_live_catalogue_cache():
    db.invalidate_live_catalogue()
    yield
    db.invalidate_live_catalogue()
