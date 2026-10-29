import os

os.environ["QUENYA_WARM_CACHES"] = "0"

import psycopg
import pytest

from app import accounts, db


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


@pytest.fixture(autouse=True)
def clear_item_mix_vector_cache():
    with db._ITEM_MIX_VECTOR_CACHE_LOCK:
        db._ITEM_MIX_VECTOR_CACHE.clear()
    yield
    with db._ITEM_MIX_VECTOR_CACHE_LOCK:
        db._ITEM_MIX_VECTOR_CACHE.clear()


@pytest.fixture
def auth_headers():
    user, invite = accounts.create_user("Test user")
    token = accounts.redeem_invite(invite)
    yield {"Authorization": f"Bearer {token}"}
    accounts.revoke_user(user["id"])
