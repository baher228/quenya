import io

import numpy as np
from app import db, embed
from PIL import Image


def test_image_source_uses_r2_public_url_when_enabled(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example")
    monkeypatch.setattr(embed.images, "r2_enabled", lambda: True)
    monkeypatch.setattr(
        embed.images,
        "r2_public_url",
        lambda path: f"https://r2.example/{path}",
    )

    assert embed._image_source(
        {"image_path": "abc.jpg", "image_url": "https://origin.example/abc.jpg"}
    ) == "https://r2.example/abc.jpg"


def test_image_source_uses_supabase_url_when_r2_is_disabled(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example/")
    monkeypatch.setattr(embed.images, "r2_enabled", lambda: False)

    assert embed._image_source(
        {"image_path": "folder/abc.jpg", "image_url": "https://origin.example/abc.jpg"}
    ) == (
        "https://supabase.example/storage/v1/object/public/quenya-img/"
        "folder/abc.jpg"
    )


def test_items_missing_embeddings_filters_orders_and_limits(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "missing.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    item_rows = [
        ("full", "2024-04-01"),
        ("missing-txt", "2024-03-01"),
        ("missing-img", "2024-02-01"),
        ("no-embedding", "2024-01-01"),
    ]
    db.insert_items(
        [
            {
                "id": item_id,
                "source": "shop.example",
                "source_url": f"https://shop.example/{item_id}",
                "title": item_id,
                "price_gbp": 20,
                "image_url": f"https://shop.example/{item_id}.jpg",
                "fetched_at": fetched_at,
            }
            for item_id, fetched_at in item_rows
        ]
    )
    vector = np.asarray([1, 0, 0, 0], dtype=np.float32)
    db.store_item_embedding("full", embed.MODEL_NAME, vector, vector, vector)
    db.store_item_embedding(
        "missing-txt", embed.MODEL_NAME, vector, None, vector
    )
    db.store_item_embedding(
        "missing-img", embed.MODEL_NAME, None, vector, vector
    )

    missing = db.items_missing_embeddings(limit=10)

    assert [item["id"] for item in missing] == [
        "missing-txt",
        "missing-img",
        "no-embedding",
    ]
    assert [item["id"] for item in db.items_missing_embeddings(limit=2)] == [
        "missing-txt",
        "missing-img",
    ]
    np.testing.assert_array_equal(missing[0]["_emb_img"], vector)
    assert missing[0]["_emb_txt"] is None
    assert missing[1]["_emb_img"] is None
    np.testing.assert_array_equal(missing[1]["_emb_txt"], vector)
    assert missing[2]["_emb_img"] is None
    assert missing[2]["_emb_txt"] is None
    db._INITIALIZED_DATABASES.clear()


def test_backfill_fills_missing_item_vectors_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "embed.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO items
               (id, source, source_url, title, image_url, fetched_at, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "item", "shop.example", "https://shop.example/item",
                "Cotton tee", "https://shop.example/image.jpg", "now", "{}",
            ),
        )
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color="navy").save(output, format="PNG")

    class Response:
        content = output.getvalue()

        @staticmethod
        def raise_for_status():
            return None

    calls = {"text": 0, "image": 0}

    def encode_texts(texts):
        calls["text"] += len(texts)
        return np.tile(np.asarray([[1, 0, 0, 0]], dtype=np.float32), (len(texts), 1))

    def encode_images(images):
        calls["image"] += len(images)
        return np.tile(np.asarray([[0, 1, 0, 0]], dtype=np.float32), (len(images), 1))

    download = {}

    def fake_get(url, **kwargs):
        download["url"] = url
        download.update(kwargs)
        return Response()

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(embed, "encode_texts", encode_texts)
    monkeypatch.setattr(embed, "encode_images", encode_images)

    embed.backfill(batch_size=1)
    vectors = db.item_embedding_map()["item"]
    assert calls == {"text": 1, "image": 1}
    assert download["headers"] == {"User-Agent": embed.images.BROWSER_UA}
    assert download["follow_redirects"] is True
    np.testing.assert_array_equal(vectors["img"], [0, 1, 0, 0])
    np.testing.assert_array_equal(vectors["txt"], [1, 0, 0, 0])
    np.testing.assert_allclose(vectors["mix"], [2**-0.5, 2**-0.5, 0, 0])

    embed.backfill(batch_size=1)
    assert calls == {"text": 1, "image": 1}
    db._INITIALIZED_DATABASES.clear()


def test_backfill_keeps_existing_image_without_loading_embedding_map(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "partial.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    db.insert_items(
        [
            {
                "id": "partial",
                "source": "shop.example",
                "source_url": "https://shop.example/partial",
                "title": "Cotton tee",
                "price_gbp": 20,
                "image_url": "https://shop.example/partial.jpg",
                "fetched_at": "2024-01-01",
            }
        ]
    )
    image_vector = np.asarray([0, 1, 0, 0], dtype=np.float32)
    text_vector = np.asarray([1, 0, 0, 0], dtype=np.float32)
    db.store_item_embedding(
        "partial", embed.MODEL_NAME, image_vector, None, image_vector
    )
    monkeypatch.setattr(
        db,
        "item_embedding_map",
        lambda: (_ for _ in ()).throw(AssertionError("full map should not load")),
    )
    monkeypatch.setattr(
        embed,
        "encode_texts",
        lambda texts: np.tile(text_vector, (len(texts), 1)),
    )
    monkeypatch.setattr(
        embed,
        "encode_images",
        lambda _images: (_ for _ in ()).throw(
            AssertionError("existing image vector should be retained")
        ),
    )
    monkeypatch.setattr(
        "httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("no image download expected")
        ),
    )

    embed.backfill(batch_size=1, limit=10)

    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT img, txt, mix FROM item_embeddings WHERE item_id=?",
            ("partial",),
        ).fetchone()
    vectors = {key: db._vector_value(row[key]) for key in ("img", "txt", "mix")}
    np.testing.assert_array_equal(vectors["img"], image_vector)
    np.testing.assert_array_equal(vectors["txt"], text_vector)
    np.testing.assert_allclose(vectors["mix"], [2**-0.5, 2**-0.5, 0, 0])
    db._INITIALIZED_DATABASES.clear()
