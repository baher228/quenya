import hashlib
from io import BytesIO
from threading import Lock
from urllib.parse import quote

from PIL import Image, ImageOps

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
_insert_lock = Lock()


def _jpeg(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def _upload_image(client, record, shop, supabase_url, secret):
    response = client.get(
        record["image_url"],
        headers={"User-Agent": UA, "Referer": record["source_url"]},
    )
    response.raise_for_status()
    content = _jpeg(response.content)
    key = hashlib.sha1(record["source_url"].encode()).hexdigest()[:16]
    path = f"{shop}/{key}.jpg"
    headers = {
        "Authorization": f"Bearer {secret}",
        "apikey": secret,
        "x-upsert": "true",
        "Content-Type": "image/jpeg",
    }
    result = client.post(
        f"{supabase_url.rstrip('/')}/storage/v1/object/quenya-listings/{quote(path, safe='/')}",
        headers=headers,
        content=content,
    )
    result.raise_for_status()
    return path


def _ensure_bucket(client, supabase_url, secret):
    headers = {"Authorization": f"Bearer {secret}", "apikey": secret}
    response = client.post(
        f"{supabase_url.rstrip('/')}/storage/v1/bucket",
        headers={**headers, "Content-Type": "application/json"},
        json={"id": "quenya-listings", "name": "quenya-listings", "public": False},
    )
    if response.status_code not in {200, 201, 409}:
        response.raise_for_status()


def upload_row(client, connection, row, shop, supabase_url, secret):
    from psycopg.types.json import Jsonb

    with _insert_lock:
        record = row["record"]
        image_path = _upload_image(client, record, shop["domain"], supabase_url, secret)
        row["image_path"] = image_path
        record["image_path"] = image_path
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO quenya.listings
                   (url, source, title, brand, price_gbp, gender, category,
                    condition, image_path, image_url_original, record)
                   VALUES (%(url)s, %(source)s, %(title)s, %(brand)s, %(price_gbp)s,
                    %(gender)s, %(category)s, %(condition)s, %(image_path)s,
                    %(image_url_original)s, %(record)s)
                   ON CONFLICT (url) DO NOTHING""",
                {**row, "record": Jsonb(record)},
            )
            inserted = cursor.rowcount
        connection.commit()
        return inserted > 0


def upload_rows(client, connection, rows, shop, supabase_url, secret):
    return [upload_row(client, connection, row, shop, supabase_url, secret) for row in rows]
