import json
import time
from dataclasses import dataclass
from threading import BoundedSemaphore
from typing import Any

import httpx
from openai import APIStatusError, OpenAI
from pydantic import BaseModel, ValidationError

from app import db
from app.config import MUBIT_API_KEY, MUBIT_BASE_URL, PROMPT_VERSION

_CALL_SEMAPHORE = BoundedSemaphore(4)


class TaskError(RuntimeError):
    def __init__(
        self, message: str, request_ids: list[str | None] | None = None,
        status_code: int | None = None
    ):
        super().__init__(message)
        self.request_ids = [request_id for request_id in (request_ids or []) if request_id]
        self.status_code = status_code


@dataclass(frozen=True)
class CallMeta:
    request_id: str | None
    model_returned: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


def _new_http_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def _log_attempt(
    *, task_name: str, model: str, meta: CallMeta, ok: bool, error: str | None
) -> None:
    db.log_llm_call(
        {
            "task": task_name,
            "model_requested": model,
            "model_returned": meta.model_returned,
            "request_id": meta.request_id,
            "latency_ms": meta.latency_ms,
            "prompt_tokens": meta.prompt_tokens,
            "completion_tokens": meta.completion_tokens,
            "ok": ok,
            "error": error,
            "prompt_version": PROMPT_VERSION,
        }
    )


def _request_id(headers: Any) -> str | None:
    try:
        return headers.get("x-middleware-request-id")
    except (AttributeError, TypeError):
        return None


def _retry_delay(headers: Any) -> float:
    try:
        value = float(headers.get("retry-after", "1"))
    except (AttributeError, TypeError, ValueError):
        value = 1.0
    return max(0.0, value)


def _call_task(
    task_name: str,
    system: str,
    model: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
) -> tuple[BaseModel, CallMeta]:
    """Call one frozen Mubit task, validate its JSON, and log each attempt."""
    db.init_db()
    client = OpenAI(
        api_key=MUBIT_API_KEY,
        base_url=MUBIT_BASE_URL,
        http_client=_new_http_client(),
        max_retries=0,
    )
    if isinstance(user_content, list) and user_content and all(
        message.get("role") in {"user", "assistant"} for message in user_content
    ):
        messages = [{"role": "system", "content": system}, *user_content]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    request_ids: list[str | None] = []
    validation_retries = 0
    busy_retries = 0

    try:
        while True:
            started = time.monotonic()
            response_id = None
            returned_model = None
            prompt_tokens = None
            completion_tokens = None
            response_headers = None
            try:
                raw_response = client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
                response_headers = raw_response.headers
                response_id = _request_id(response_headers)
                parsed_response = raw_response.parse()
                returned_model = parsed_response.model
                if parsed_response.usage:
                    prompt_tokens = parsed_response.usage.prompt_tokens
                    completion_tokens = parsed_response.usage.completion_tokens
                content = parsed_response.choices[0].message.content
                if not isinstance(content, str):
                    raise ValueError("Mubit response had no JSON text content")
                try:
                    payload = json.loads(content)
                    parsed = out_model.model_validate(payload)
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    meta = CallMeta(
                        response_id, returned_model, round((time.monotonic() - started) * 1000),
                        prompt_tokens, completion_tokens
                    )
                    request_ids.append(response_id)
                    _log_attempt(
                        task_name=task_name,
                        model=model,
                        meta=meta,
                        ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    if validation_retries < 1:
                        validation_retries += 1
                        continue
                    raise TaskError(
                        f"{task_name} returned invalid JSON/schema after retry: {exc}",
                        request_ids,
                    ) from exc
                meta = CallMeta(
                    response_id, returned_model, round((time.monotonic() - started) * 1000),
                    prompt_tokens, completion_tokens
                )
                request_ids.append(response_id)
                _log_attempt(
                    task_name=task_name, model=model, meta=meta, ok=True, error=None
                )
                return parsed, meta
            except APIStatusError as exc:
                response_headers = exc.response.headers if exc.response is not None else None
                response_id = _request_id(response_headers)
                meta = CallMeta(
                    response_id,
                    returned_model,
                    round((time.monotonic() - started) * 1000),
                    prompt_tokens,
                    completion_tokens,
                )
                request_ids.append(response_id)
                message = f"HTTP {exc.status_code}: {exc}"
                _log_attempt(
                    task_name=task_name, model=model, meta=meta, ok=False, error=message
                )
                if exc.status_code in (429, 503) and busy_retries < 2:
                    busy_retries += 1
                    time.sleep(_retry_delay(response_headers))
                    continue
                raise TaskError(
                    f"{task_name} failed: {message}", request_ids, exc.status_code
                ) from exc
            except TaskError:
                raise
            except Exception as exc:
                meta = CallMeta(
                    response_id,
                    returned_model,
                    round((time.monotonic() - started) * 1000),
                    prompt_tokens,
                    completion_tokens,
                )
                request_ids.append(response_id)
                message = f"{type(exc).__name__}: {exc}"
                _log_attempt(
                    task_name=task_name, model=model, meta=meta, ok=False, error=message
                )
                raise TaskError(f"{task_name} failed: {message}", request_ids) from exc
    finally:
        client.close()


def call_task(
    task_name: str,
    system: str,
    model: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
) -> tuple[BaseModel, CallMeta]:
    with _CALL_SEMAPHORE:
        return _call_task(task_name, system, model, user_content, out_model)
