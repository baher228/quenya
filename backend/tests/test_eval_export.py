import gzip
import hashlib
import json
import sqlite3

import numpy as np
import pytest

from app import db, eval_export
from app.replay_eval import load_export


@pytest.fixture(autouse=True)
def fresh_postgres_schema():
    yield


@pytest.fixture
def seeded_eval_db(monkeypatch, tmp_path):
    database = tmp_path / "eval.sqlite"
    monkeypatch.delenv("QUENYA_DATABASE_URL", raising=False)
    monkeypatch.delenv("QUENYA_CLIP_TEXT_PATH", raising=False)
    monkeypatch.delenv("QUENYA_CLIP_TEXT_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(eval_export, "DB_PATH", database)
    db._INITIALIZED_DATABASES.clear()
    db.init_db()

    owners = (("user-alice", "profile-alice", "Alice Secret"),
              ("user-bob", "profile-bob", "Bob Secret"))
    for owner_id, profile_id, name in owners:
        with db.db_connection() as connection:
            connection.execute(
                "INSERT INTO users(id, name, role, created_at) VALUES (?, ?, ?, ?)",
                (owner_id, name, "tester", "2025-01-01T00:00:00Z"),
            )
        db.create_profile(
            profile_id,
            {"text": "my private words", "gender": "both"},
            owner_id=owner_id,
            ranker_arm="embed",
        )

    item_ids = ("alice-one", "alice-two", "alice-deck",
                "bob-one", "bob-two", "bob-deck")
    for index, item_id in enumerate(item_ids):
        db.insert_items([{
            "id": item_id,
            "source": "shop.example",
            "source_url": f"https://shop.example/items/{index}",
            "title": f"Garment {index}",
            "image_url": f"https://shop.example/images/{index}.jpg",
            "price_gbp": 25,
            "fetched_at": "2025-01-01T00:00:00Z",
            "availability": "InStock",
            "query": "live:profile-alice" if item_id == "alice-one" else None,
        }])
        db.save_tags(item_id, {
            "slot": "top",
            "gender": "unisex",
            "condition": "new",
            "aesthetics": ["casual"],
            "colours": ["blue"],
            "materials": ["cotton"],
            "niche": 0,
        }, "test-tags")

    swipe_items = (
        ("profile-alice", "alice-one", True),
        ("profile-alice", "alice-two", False),
        ("profile-bob", "bob-one", True),
        ("profile-bob", "bob-two", False),
    )
    for index, (profile_id, item_id, liked) in enumerate(swipe_items):
        db.insert_swipe(profile_id, item_id, liked)
        with db.db_connection() as connection:
            connection.execute(
                "UPDATE swipes SET created_at=? WHERE profile_id=? AND item_id=?",
                (f"2025-01-01T00:00:0{index}Z", profile_id, item_id),
            )

    for profile_id in ("profile-alice", "profile-bob"):
        db.save_taste_version(
            profile_id, 1, {"likes": {"casual": 0.5}}, {}, "test-request"
        )
        with db.db_connection() as connection:
            connection.execute(
                "UPDATE taste_versions SET created_at=? WHERE profile_id=?",
                ("2025-01-01T00:00:05Z", profile_id),
            )

    db.record_deck_events([
        {
            "id": "deck-alice",
            "profile_id": "profile-alice",
            "item_id": "alice-deck",
            "arm": "embed",
            "policy": "prod",
            "position": 1,
            "score": 0.75,
            "created_at": "2025-01-01T00:00:06Z",
        },
        {
            "id": "deck-bob",
            "profile_id": "profile-bob",
            "item_id": "bob-deck",
            "arm": "tags",
            "policy": "prod",
            "position": 2,
            "score": 0.25,
            "created_at": "2025-01-01T00:00:06Z",
        },
    ])

    vectors = {}
    for index, item_id in enumerate(
        ("alice-one", "alice-two", "alice-deck", "bob-one")
    ):
        vector = np.arange(8, dtype=np.float32) + index / 10
        vectors[item_id] = vector
        db.store_item_embedding(item_id, "clip32", vector, vector, vector)
    db.store_profile_embedding(
        "profile-alice", "clip32", np.arange(8, dtype=np.float32), "brief-a"
    )
    db.store_profile_embedding(
        "profile-bob", "clip32", np.arange(8, dtype=np.float32) + 1, "brief-b"
    )
    return database, vectors


def _records(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _anon(salt, value):
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:16]


def test_export_anonymizes_records_and_preserves_vector_values(
    seeded_eval_db, tmp_path
):
    database, vectors = seeded_eval_db
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    path = tmp_path / "first.jsonl.gz"
    salt = "fixed-export-salt"

    counts = eval_export.export(path, salt=salt, sample_size=2)
    content = gzip.open(path, "rt", encoding="utf-8").read()

    for private_value in (
        "profile-alice", "profile-bob", "user-alice", "user-bob",
        "Alice Secret", "Bob Secret", "my private words",
    ):
        assert private_value not in content

    records = _records(path)
    assert counts == {
        "meta": 1,
        "style": 2,
        "brief_vector": 2,
        "swipe": 4,
        "deck_event": 2,
        "taste": 2,
        "item": 6,
        "vector": 4,
        "sample": 1,
    }
    styles = [record for record in records if record["kind"] == "style"]
    assert all(record["profile_kind"] == "user" for record in styles)
    loaded = load_export(path)
    assert len(loaded.styles) == 2
    assert all(style.kind == "user" for style in loaded.styles.values())
    assert {record["owner"] for record in styles} == {
        _anon(salt, "user-alice"),
        _anon(salt, "user-bob"),
    }
    assert _anon(salt, "user-alice") != _anon(salt, "user-bob")
    assert all(record["brief"] == {"gender": "both"} for record in styles)

    live_item = next(
        record for record in records
        if record["kind"] == "item" and record["id"] == "alice-one"
    )
    assert live_item["query"] == f"live:{_anon(salt, 'profile-alice')}"

    vector_records = [record for record in records if record["kind"] == "vector"]
    for record in vector_records:
        np.testing.assert_allclose(
            eval_export.decode_vector(record["v"]),
            vectors[record["item_id"]],
            atol=1e-3,
            rtol=1e-3,
        )
    brief_vectors = [
        record for record in records if record["kind"] == "brief_vector"
    ]
    assert len(brief_vectors) == 2
    expected_briefs = {
        _anon(salt, "profile-alice"): np.arange(8, dtype=np.float32),
        _anon(salt, "profile-bob"): np.arange(8, dtype=np.float32) + 1,
    }
    for record in brief_vectors:
        np.testing.assert_allclose(
            eval_export.decode_vector(record["v"]),
            expected_briefs[record["style"]],
            atol=1e-3,
            rtol=1e-3,
        )

    second_path = tmp_path / "second.jsonl.gz"
    eval_export.export(second_path, salt=salt, sample_size=2)
    second_styles = [record for record in _records(second_path) if record["kind"] == "style"]
    assert {record["style"]: record["owner"] for record in styles} == {
        record["style"]: record["owner"] for record in second_styles
    }
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_include_brief_text_is_opt_in_and_sqlite_connection_is_readonly(
    seeded_eval_db, tmp_path
):
    database, _vectors = seeded_eval_db
    path = tmp_path / "with-brief.jsonl.gz"
    eval_export.export(path, include_brief_text=True, salt="another-salt")
    content = gzip.open(path, "rt", encoding="utf-8").read()
    assert content.count("my private words") == 2

    source = eval_export._ReadOnly(None)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            source.connection.execute(
                "INSERT INTO users(id, name, created_at) VALUES ('x', 'x', 'x')"
            )
    finally:
        source.close()
