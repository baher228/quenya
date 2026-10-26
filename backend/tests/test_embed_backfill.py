import io

import numpy as np
from PIL import Image

from app import db, embed


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

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(embed, "encode_texts", encode_texts)
    monkeypatch.setattr(embed, "encode_images", encode_images)

    embed.backfill(batch_size=1)
    vectors = db.item_embedding_map()["item"]
    assert calls == {"text": 1, "image": 1}
    np.testing.assert_array_equal(vectors["img"], [0, 1, 0, 0])
    np.testing.assert_array_equal(vectors["txt"], [1, 0, 0, 0])
    np.testing.assert_allclose(vectors["mix"], [2**-0.5, 2**-0.5, 0, 0])

    embed.backfill(batch_size=1)
    assert calls == {"text": 1, "image": 1}
    db._INITIALIZED_DATABASES.clear()
