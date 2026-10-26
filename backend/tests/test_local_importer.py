import json

import numpy as np

from app import db, embedding, images, local_tagger, supabase_import
from app.local_tagger import LocalTagger


def _tags():
    return {
        "slot": "top",
        "category": "tee",
        "colours": ["black"],
        "materials": ["cotton"],
        "fit": "regular",
        "era": "contemporary",
        "aesthetics": ["streetwear"],
        "formality": 3,
        "niche": 0.4,
        "gender": "unisex",
        "logo_heavy": False,
        "condition": "new",
        "one_line": "tee",
    }


def _row():
    return {
        "url": "https://shop.example/item-1",
        "source": "shop.example",
        "title": "Cotton tee",
        "brand": "Shop",
        "price_gbp": 20,
        "currency": "GBP",
        "image_url_original": "https://shop.example/item-1.jpg",
        "record": {
            "id": "item-1",
            "title": "Cotton tee",
            "image_url": "https://shop.example/item-1.jpg",
            "price_gbp": 20,
            "currency": "GBP",
        },
    }


def _configure_local_import(monkeypatch, tmp_path, db_path):
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    monkeypatch.setattr(supabase_import, "_download_original_image", lambda _url: b"image")

    def cache_image(*_args):
        path = tmp_path / "image.jpg"
        path.write_bytes(b"image")
        return str(path)

    monkeypatch.setattr(supabase_import, "_cache_image", cache_image)
    monkeypatch.setattr(images, "display_storage_enabled", lambda: False)
    vector = np.asarray([1, 0, 0, 0], dtype=np.float32)
    monkeypatch.setattr(embedding, "encode_images", lambda _images: vector[None, :])
    monkeypatch.setattr(embedding, "encode_texts", lambda _texts: vector[None, :])
    prompts = np.zeros((len(local_tagger.COLOURS), 4), dtype=np.float32)
    prompts[0, 0] = 1
    monkeypatch.setattr(local_tagger, "_colour_prompt_vectors", lambda: prompts)
    tagger = LocalTagger([(vector, _tags())])
    constructed = []
    monkeypatch.setattr(
        LocalTagger, "from_db",
        classmethod(lambda cls: (constructed.append(True) or tagger)),
    )
    return constructed


def test_local_import_inserts_tags_and_embedding_atomically(monkeypatch, tmp_path):
    constructed = _configure_local_import(monkeypatch, tmp_path, tmp_path / "local.sqlite")
    source = tmp_path / "rows.jsonl"
    source.write_text(json.dumps(_row()) + "\n")
    connection = db.connect()

    result = supabase_import.run_insert_only_file_import(
        [source], connection, set(), tagger="local",
    )

    row = connection.execute(
        "SELECT tags_request_id FROM items WHERE id='item-1'"
    ).fetchone()
    vectors = db.item_embedding_map()
    connection.close()
    assert result["inserted"] == 1
    assert row["tags_request_id"] == local_tagger.LOCAL_TAGGER_ID
    assert vectors["item-1"]["img"] is not None
    assert vectors["item-1"]["txt"] is not None
    assert len(constructed) == 1


def test_failed_local_item_insert_leaves_no_embedding(monkeypatch, tmp_path):
    _configure_local_import(monkeypatch, tmp_path, tmp_path / "failed.sqlite")
    source = tmp_path / "rows.jsonl"
    source.write_text(json.dumps(_row()) + "\n")
    connection = db.connect()
    connection.execute(
        """CREATE TRIGGER reject_item BEFORE INSERT ON items
           BEGIN SELECT RAISE(ABORT, 'forced item insert failure'); END"""
    )
    connection.commit()

    result = supabase_import.run_insert_only_file_import(
        [source], connection, set(), tagger="local",
    )

    assert result["failed"] == 1
    assert connection.execute("SELECT COUNT(*) FROM item_embeddings").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    connection.close()
