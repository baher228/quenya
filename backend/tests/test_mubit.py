import json

import httpx
import pytest

from app import db, mubit
from app.schemas import ItemTags

VALID_TAGS = {
    "slot": "outerwear",
    "category": "chore jacket",
    "colours": ["olive"],
    "materials": ["cotton"],
    "fit": "relaxed",
    "era": "contemporary",
    "aesthetics": ["japanese workwear"],
    "formality": 2,
    "niche": 0.6,
    "gender": "unisex",
    "logo_heavy": False,
    "condition": "new",
    "one_line": "A practical layer with a relaxed shape.",
}


def response(payload, request_id):
    return httpx.Response(
        200,
        headers={"x-middleware-request-id": request_id},
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32},
        },
    )


def configure_test_client(monkeypatch, tmp_path, handler):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(mubit, "MUBIT_API_KEY", "test-only-key")
    monkeypatch.setattr(
        mubit,
        "_new_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_call_task_captures_mubit_request_id(monkeypatch, tmp_path):
    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        return response(VALID_TAGS, "mreq_test_valid")

    configure_test_client(monkeypatch, tmp_path, handler)
    parsed, meta = mubit.call_task("tagger", "system", "gpt-4.1-mini", "listing", ItemTags)
    assert parsed.slot == "outerwear"
    assert meta.request_id == "mreq_test_valid"
    with db.db_connection() as connection:
        row = connection.execute("SELECT request_id, ok FROM llm_calls").fetchone()
    assert tuple(row) == ("mreq_test_valid", 1)


def test_invalid_reply_retries_once_and_logs_both_failures(monkeypatch, tmp_path):
    request_ids = iter(("mreq_invalid_one", "mreq_invalid_two"))
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return response({**VALID_TAGS, "slot": "invalid"}, next(request_ids))

    configure_test_client(monkeypatch, tmp_path, handler)
    with pytest.raises(mubit.TaskError) as exc_info:
        mubit.call_task("tagger", "system", "gpt-4.1-mini", "listing", ItemTags)
    assert call_count == 2
    assert exc_info.value.request_ids == ["mreq_invalid_one", "mreq_invalid_two"]
    with db.db_connection() as connection:
        rows = connection.execute(
            "SELECT request_id, ok, error FROM llm_calls ORDER BY id"
        ).fetchall()
    assert [row["request_id"] for row in rows] == [
        "mreq_invalid_one",
        "mreq_invalid_two",
    ]
    assert all(row["ok"] == 0 and "ValidationError" in row["error"] for row in rows)
