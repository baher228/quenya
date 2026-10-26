import base64
import json
import stat

import httpx
import pytest

from app import db, llm
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


def openai_response(payload, request_id):
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


def configure_openai(monkeypatch, tmp_path, handler):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "openai.sqlite")
    monkeypatch.setattr(llm, "QUENYA_LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm, "QUENYA_LLM_API_KEY", "test-only-key")
    monkeypatch.setattr(llm, "QUENYA_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(llm, "QUENYA_LLM_DAILY_CAP", "300")
    monkeypatch.setattr(
        llm,
        "_new_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def configure_cursor(monkeypatch, tmp_path, responses, *, delay=0):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cursor.sqlite")
    monkeypatch.setattr(llm, "QUENYA_LLM_PROVIDER", "cursor")
    monkeypatch.setattr(llm, "QUENYA_CURSOR_MODEL", "gemini-test")
    monkeypatch.setattr(llm, "QUENYA_CURSOR_TIMEOUT_S", "2")
    monkeypatch.setattr(llm, "QUENYA_LLM_DAILY_CAP", "300")
    monkeypatch.setattr(llm, "_daily_call_count", lambda: 0)

    binary = tmp_path / "cursor-agent"
    response_path = tmp_path / "responses.json"
    count_path = tmp_path / "invocations.txt"
    capture_path = tmp_path / "capture.jsonl"
    response_path.write_text(json.dumps(responses), encoding="utf-8")
    script = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

count_path = pathlib.Path(os.environ["FAKE_CURSOR_COUNT"])
count_path.parent.mkdir(parents=True, exist_ok=True)
with count_path.open("a", encoding="utf-8") as count_file:
    count_file.write("call\\n")
index = len(count_path.read_text(encoding="utf-8").splitlines()) - 1
capture_path = pathlib.Path(os.environ["FAKE_CURSOR_CAPTURE"])
with capture_path.open("a", encoding="utf-8") as capture_file:
    capture_file.write(json.dumps({
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "files": sorted(os.listdir()),
    }) + "\\n")
time.sleep(float(os.environ.get("FAKE_CURSOR_DELAY", "0")))
responses = json.loads(pathlib.Path(os.environ["FAKE_CURSOR_RESPONSES"]).read_text())
print(json.dumps(responses[min(index, len(responses) - 1)]))
"""
    binary.write_text(script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setattr(llm, "QUENYA_CURSOR_BIN", str(binary))
    monkeypatch.setenv("FAKE_CURSOR_RESPONSES", str(response_path))
    monkeypatch.setenv("FAKE_CURSOR_COUNT", str(count_path))
    monkeypatch.setenv("FAKE_CURSOR_CAPTURE", str(capture_path))
    monkeypatch.setenv("FAKE_CURSOR_DELAY", str(delay))
    return count_path, capture_path


def cursor_response(result, *, request_id="cursor-request-1", is_error=False):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "duration_ms": 20,
        "result": result,
        "session_id": "session-test",
        "request_id": request_id,
        "usage": {
            "inputTokens": 123,
            "outputTokens": 45,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
        },
    }


def test_openai_provider_preserves_request_id_and_json_mode(monkeypatch, tmp_path):
    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        return openai_response(VALID_TAGS, "openai-request")

    configure_openai(monkeypatch, tmp_path, handler)
    parsed, meta = llm.call_task(
        "tagger", "system", "gpt-4.1-mini", "listing", ItemTags
    )
    assert parsed.slot == "outerwear"
    assert meta.request_id == "openai-request"
    with db.db_connection() as connection:
        row = connection.execute("SELECT request_id, ok FROM llm_calls").fetchone()
    assert tuple(row) == ("openai-request", 1)


def test_openai_invalid_reply_retries_once_and_logs_both_attempts(
    monkeypatch, tmp_path
):
    request_ids = iter(("openai-invalid-one", "openai-invalid-two"))
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return openai_response(
            {**VALID_TAGS, "slot": "invalid"}, next(request_ids)
        )

    configure_openai(monkeypatch, tmp_path, handler)
    with pytest.raises(llm.TaskError) as exc_info:
        llm.call_task("tagger", "system", "gpt-4.1-mini", "listing", ItemTags)
    assert calls == 2
    assert exc_info.value.request_ids == ["openai-invalid-one", "openai-invalid-two"]
    with db.db_connection() as connection:
        rows = connection.execute(
            "SELECT request_id, ok, error FROM llm_calls ORDER BY id"
        ).fetchall()
    assert [row["request_id"] for row in rows] == [
        "openai-invalid-one",
        "openai-invalid-two",
    ]
    assert all(row["ok"] == 0 and "ValidationError" in row["error"] for row in rows)


def test_cursor_parses_fenced_json(monkeypatch, tmp_path):
    response = cursor_response(f"```json\n{json.dumps(VALID_TAGS)}\n```")
    configure_cursor(monkeypatch, tmp_path, [response])

    parsed, meta = llm.call_task(
        "tagger", "system", "ignored-openai-model", "listing", ItemTags
    )

    assert parsed.slot == "outerwear"
    assert meta.request_id == "cursor-request-1"
    assert meta.model_returned == "gemini-test"
    assert meta.prompt_tokens == 123
    assert meta.completion_tokens == 45


def test_cursor_extracts_json_from_surrounding_prose(monkeypatch, tmp_path):
    response = cursor_response(f"Here is the result: {json.dumps(VALID_TAGS)} Thanks.")
    configure_cursor(monkeypatch, tmp_path, [response])

    parsed, _meta = llm.call_task(
        "router", "system", "ignored-openai-model", "input", ItemTags
    )

    assert parsed.category == "chore jacket"


def test_cursor_is_error_raises_task_error_without_retry(monkeypatch, tmp_path):
    response = cursor_response(
        "Authentication failed",
        request_id="cursor-auth-failure",
        is_error=True,
    )
    count_path, _capture_path = configure_cursor(monkeypatch, tmp_path, [response])

    with pytest.raises(llm.TaskError, match="Authentication failed") as exc_info:
        llm.call_task("tagger", "system", "model", "listing", ItemTags)

    assert exc_info.value.request_ids == ["cursor-auth-failure"]
    assert len(count_path.read_text(encoding="utf-8").splitlines()) == 1


def test_cursor_timeout_retries_once_then_raises(monkeypatch, tmp_path):
    count_path, _capture_path = configure_cursor(
        monkeypatch, tmp_path, [cursor_response(json.dumps(VALID_TAGS))], delay=0.2
    )
    monkeypatch.setattr(llm, "QUENYA_CURSOR_TIMEOUT_S", "0.05")

    with pytest.raises(llm.TaskError, match="timed out"):
        llm.call_task("tagger", "system", "model", "listing", ItemTags)

    assert len(count_path.read_text(encoding="utf-8").splitlines()) == 2


def test_cursor_data_url_is_saved_and_named_in_prompt(monkeypatch, tmp_path):
    count_path, capture_path = configure_cursor(
        monkeypatch, tmp_path, [cursor_response(json.dumps(VALID_TAGS))]
    )
    image_bytes = b"\x89PNG\r\n\x1a\nfixture"
    image_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode()

    parsed, _meta = llm.call_task(
        "tagger",
        "system",
        "model",
        [
            {"type": "text", "text": "A jacket"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
        ItemTags,
    )

    captured = json.loads(capture_path.read_text(encoding="utf-8").splitlines()[0])
    prompt = captured["argv"][-1]
    assert parsed.slot == "outerwear"
    assert len(count_path.read_text(encoding="utf-8").splitlines()) == 1
    assert "image_1.png" in captured["files"]
    assert "image_1.png" in prompt
    assert "Do not use any tools except reading those image files." in prompt


def test_invalid_cursor_schema_uses_repair_retry(monkeypatch, tmp_path):
    first = cursor_response(
        json.dumps({**VALID_TAGS, "slot": "not-a-slot"}),
        request_id="cursor-invalid",
    )
    second = cursor_response(json.dumps(VALID_TAGS), request_id="cursor-repaired")
    count_path, capture_path = configure_cursor(monkeypatch, tmp_path, [first, second])

    parsed, meta = llm.call_task(
        "tagger", "system", "model", "listing", ItemTags
    )

    captures = [json.loads(line) for line in capture_path.read_text().splitlines()]
    assert parsed.slot == "outerwear"
    assert meta.request_id == "cursor-repaired"
    assert len(count_path.read_text().splitlines()) == 2
    assert "previous response failed validation" in captures[1]["argv"][-1]


def test_daily_cap_stops_before_cursor_executable(monkeypatch, tmp_path):
    count_path, _capture_path = configure_cursor(
        monkeypatch, tmp_path, [cursor_response(json.dumps(VALID_TAGS))]
    )
    monkeypatch.setattr(llm, "QUENYA_LLM_DAILY_CAP", "3")
    monkeypatch.setattr(llm, "_daily_call_count", lambda: 3)

    with pytest.raises(llm.TaskError, match="llm_daily_cap"):
        llm.call_task("tagger", "system", "model", "listing", ItemTags)

    assert not count_path.exists()
