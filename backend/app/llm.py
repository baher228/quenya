import base64
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import unquote_to_bytes, urlsplit

import httpx
from openai import APIStatusError, OpenAI
from pydantic import BaseModel, ValidationError

from app import db
from app.config import (
    CURSOR_API_KEY,
    PROMPT_VERSION,
    QUENYA_CURSOR_BIN,
    QUENYA_CURSOR_MODEL,
    QUENYA_CURSOR_STYLIST_MODEL,
    QUENYA_CURSOR_TIMEOUT_S,
    QUENYA_LLM_API_KEY,
    QUENYA_LLM_BASE_URL,
    QUENYA_LLM_DAILY_CAP,
    QUENYA_LLM_PROVIDER,
)

_CALL_SEMAPHORE = BoundedSemaphore(4)
_CURSOR_RETRIES = 1
_VALIDATION_RETRIES = 1
_IMAGE_TIMEOUT_S = 20
_REQUIRED_CURSOR_FIELDS = {"is_error", "result", "request_id", "usage"}


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


class _CursorFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        meta: CallMeta,
        retryable: bool = False,
        validation_error: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.meta = meta
        self.retryable = retryable
        self.validation_error = validation_error
        self.status_code = status_code


def _new_http_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def _safe_error(message: Any) -> str:
    result = str(message)
    for secret in (
        CURSOR_API_KEY,
        QUENYA_LLM_API_KEY,
        os.getenv("CURSOR_API_KEY", ""),
        os.getenv("QUENYA_LLM_API_KEY", ""),
    ):
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


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
            "error": _safe_error(error) if error else None,
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


def _daily_call_count() -> int:
    db.init_db()
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS call_count FROM llm_calls "
            "WHERE created_at >= ? AND created_at < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
    return int(row["call_count"] if hasattr(row, "keys") else row[0])


def _check_daily_cap() -> None:
    try:
        limit = int(QUENYA_LLM_DAILY_CAP)
    except (TypeError, ValueError) as exc:
        raise TaskError("Invalid QUENYA_LLM_DAILY_CAP; expected an integer") from exc
    if limit < 0:
        raise TaskError("Invalid QUENYA_LLM_DAILY_CAP; expected a non-negative integer")
    if _daily_call_count() >= limit:
        raise TaskError(
            f"llm_daily_cap: daily limit of {limit} LLM calls reached for the UTC day"
        )


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _failure_meta(
    request_id: str | None, model: str, started: float,
    prompt_tokens: int | None = None, completion_tokens: int | None = None,
) -> CallMeta:
    return CallMeta(
        request_id,
        model,
        round((time.monotonic() - started) * 1000),
        prompt_tokens,
        completion_tokens,
    )


def _call_openai_task(
    task_name: str,
    system: str,
    model: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
    log_attempts: bool,
) -> tuple[BaseModel, CallMeta]:
    if log_attempts:
        db.init_db()
    client = OpenAI(
        api_key=QUENYA_LLM_API_KEY,
        base_url=QUENYA_LLM_BASE_URL,
        http_client=_new_http_client(),
        max_retries=0,
    )
    if isinstance(user_content, list) and user_content and all(
        isinstance(message, dict) and message.get("role") in {"user", "assistant"}
        for message in user_content
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
            _check_daily_cap()
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
                    raise ValueError("LLM response had no JSON text content")
                try:
                    parsed = out_model.model_validate(json.loads(content))
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    meta = _failure_meta(
                        response_id, returned_model or model, started,
                        prompt_tokens, completion_tokens,
                    )
                    request_ids.append(response_id)
                    if log_attempts:
                        _log_attempt(
                            task_name=task_name, model=model, meta=meta, ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    if validation_retries < _VALIDATION_RETRIES:
                        validation_retries += 1
                        continue
                    raise TaskError(
                        f"{task_name} returned invalid JSON/schema after retry: {exc}",
                        request_ids,
                    ) from exc
                meta = _failure_meta(
                    response_id, returned_model or model, started,
                    prompt_tokens, completion_tokens,
                )
                request_ids.append(response_id)
                if log_attempts:
                    _log_attempt(
                        task_name=task_name, model=model, meta=meta, ok=True, error=None
                    )
                return parsed, meta
            except APIStatusError as exc:
                response_headers = exc.response.headers if exc.response is not None else None
                response_id = _request_id(response_headers)
                meta = _failure_meta(
                    response_id, returned_model or model, started,
                    prompt_tokens, completion_tokens,
                )
                request_ids.append(response_id)
                message = _safe_error(f"HTTP {exc.status_code}: {exc}")
                if log_attempts:
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
                meta = _failure_meta(
                    response_id, returned_model or model, started,
                    prompt_tokens, completion_tokens,
                )
                request_ids.append(response_id)
                message = _safe_error(f"{type(exc).__name__}: {exc}")
                if log_attempts:
                    _log_attempt(
                        task_name=task_name, model=model, meta=meta, ok=False, error=message
                    )
                raise TaskError(f"{task_name} failed: {message}", request_ids) from exc
    finally:
        client.close()


def _text_and_images(
    user_content: str | list[dict[str, Any]],
) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    image_urls: list[str] = []

    def add_part(part: Any, role: str | None = None) -> None:
        if isinstance(part, str):
            text_parts.append(f"{role.upper()}: {part}" if role else part)
            return
        if isinstance(part, list):
            for child in part:
                add_part(child, role)
            return
        if not isinstance(part, dict):
            return
        part_type = part.get("type")
        if part_type == "text" and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        elif part_type == "image_url":
            image_part = part.get("image_url")
            url = image_part.get("url") if isinstance(image_part, dict) else image_part
            if isinstance(url, str):
                image_urls.append(url)
        elif "role" in part and "content" in part:
            add_part(part["content"], str(part["role"]))
        elif "content" in part:
            add_part(part["content"], role)

    if isinstance(user_content, str):
        text_parts.append(user_content)
    else:
        for part in user_content:
            add_part(part)
    return "\n".join(text_parts), image_urls


def _image_extension(content_type: str | None, url: str) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    known = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
        "image/tiff": "tiff",
        "image/avif": "avif",
    }
    if media_type in known:
        return known[media_type]
    guessed = mimetypes.guess_extension(media_type) if media_type else None
    if guessed:
        return guessed.lstrip(".")
    suffix = Path(urlsplit(url).path).suffix.lstrip(".").lower()
    return suffix if re.fullmatch(r"[a-z0-9]{1,8}", suffix) else "jpg"


def _write_cursor_images(image_urls: list[str], directory: Path) -> list[str]:
    names = []
    for index, url in enumerate(image_urls, 1):
        if url.startswith("data:"):
            header, separator, payload = url.partition(",")
            if not separator:
                raise ValueError("Invalid image data URL")
            metadata = header[5:].split(";")
            content_type = metadata[0] or "image/jpeg"
            if "base64" in metadata[1:]:
                content = base64.b64decode(payload, validate=True)
            else:
                content = unquote_to_bytes(payload)
        elif url.startswith(("http://", "https://")):
            response = httpx.get(url, timeout=_IMAGE_TIMEOUT_S, follow_redirects=True)
            response.raise_for_status()
            content = response.content
            content_type = response.headers.get("content-type")
        else:
            raise ValueError("Cursor image URLs must use data:, http://, or https://")
        name = f"image_{index}.{_image_extension(content_type, url)}"
        (directory / name).write_bytes(content)
        names.append(name)
    return names


def _cursor_binary() -> str:
    return (
        QUENYA_CURSOR_BIN
        or shutil.which("cursor-agent")
        or str(Path.home() / ".local" / "bin" / "cursor-agent")
    )


def _cursor_timeout() -> float:
    try:
        timeout = float(QUENYA_CURSOR_TIMEOUT_S)
    except (TypeError, ValueError) as exc:
        raise TaskError("Invalid QUENYA_CURSOR_TIMEOUT_S; expected seconds") from exc
    if timeout <= 0:
        raise TaskError("Invalid QUENYA_CURSOR_TIMEOUT_S; expected a positive value")
    return timeout


def _kill_cursor_process_group(process_id: int) -> None:
    try:
        os.killpg(process_id, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "unauthorized", "unauthorised", "authentication", "not authenticated",
            "invalid api key", "api key is invalid", "forbidden", "http 401", "http 403",
            "status 401", "status 403",
        )
    )


def _transient_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "timeout", "timed out", "temporarily", "temporary", "rate limit",
            "http 429", "http 502", "http 503", "http 504", "connection reset",
            "connection refused", "status 429", "status 502", "status 503",
            "status 504", "network", "socket", "econn",
        )
    )


def _cursor_meta(outer: dict[str, Any], model: str, started: float) -> CallMeta:
    usage = outer.get("usage")
    request_id = outer.get("request_id")
    return _failure_meta(
        request_id if isinstance(request_id, str) else None,
        str(outer.get("model") or model),
        started,
        _as_int(usage.get("inputTokens")) if isinstance(usage, dict) else None,
        _as_int(usage.get("outputTokens")) if isinstance(usage, dict) else None,
    )


def _cursor_once(
    system: str,
    model: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
    repair_error: str | None,
) -> tuple[BaseModel, CallMeta]:
    text, image_urls = _text_and_images(user_content)
    try:
        timeout = _cursor_timeout()
    except TaskError:
        raise
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        started = time.monotonic()
        try:
            image_names = _write_cursor_images(image_urls, directory)
            prompt = system + "\n\n---\nINPUT:\n" + text
            if image_names:
                prompt += (
                    "\nImages: look at the files "
                    + ", ".join(image_names)
                    + " in the current directory."
                )
            prompt += (
                "\nDo not use any tools except reading those image files. Reply with "
                "ONLY one JSON object (no prose, no code fences) that validates "
                "against this JSON Schema:\n"
                + json.dumps(out_model.model_json_schema())
            )
            if repair_error:
                prompt += (
                    "\n\nYour previous response failed validation: "
                    + repair_error
                    + ". Return corrected JSON that satisfies the schema."
                )
            argv = [
                _cursor_binary(),
                "-p",
                "--trust",
                "--mode",
                "ask",
                "--model",
                model,
                "--output-format",
                "json",
                prompt,
            ]
            proc = subprocess.Popen(
                argv,
                cwd=directory,
                env=os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                try:
                    stdout, stderr = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill_cursor_process_group(proc.pid)
                    proc.communicate()
                    raise
            finally:
                _kill_cursor_process_group(proc.pid)
            completed = subprocess.CompletedProcess(
                argv, proc.returncode, stdout, stderr
            )
        except subprocess.TimeoutExpired as exc:
            message = f"cursor-agent timed out after {timeout:g}s"
            raise _CursorFailure(
                message,
                meta=_failure_meta(None, model, started),
                retryable=True,
            ) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            message = _safe_error(f"{type(exc).__name__}: {exc}")
            raise _CursorFailure(
                message, meta=_failure_meta(None, model, started), retryable=True
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            message = _safe_error(f"Image download HTTP {status_code}: {exc}")
            raise _CursorFailure(
                message,
                meta=_failure_meta(None, model, started),
                retryable=status_code >= 500 or status_code == 429,
                status_code=status_code,
            ) from exc
        except OSError as exc:
            message = _safe_error(f"{type(exc).__name__}: {exc}")
            raise _CursorFailure(
                message, meta=_failure_meta(None, model, started), retryable=False
            ) from exc
        except ValueError as exc:
            message = _safe_error(f"ValueError: {exc}")
            raise _CursorFailure(
                message, meta=_failure_meta(None, model, started), retryable=False
            ) from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError as exc:
        message = _safe_error(
            f"cursor-agent returned invalid outer JSON: {stderr or stdout[:500]}"
        )
        auth_failure = _auth_error(message)
        raise _CursorFailure(
            message,
            meta=_failure_meta(None, model, started),
            retryable=not auth_failure,
            status_code=401 if auth_failure and "401" in message else (
                403 if auth_failure and "403" in message else None
            ),
        ) from exc
    if not isinstance(outer, dict):
        raise _CursorFailure(
            "cursor-agent returned an unexpected JSON value",
            meta=_failure_meta(None, model, started),
        )
    meta = _cursor_meta(outer, model, started)
    detail = _safe_error(
        " ".join(
            value for value in (str(outer.get("result") or ""), stderr.strip()) if value
        )
    )
    if completed.returncode != 0:
        if not detail:
            detail = f"cursor-agent exited with status {completed.returncode}"
        auth_failure = _auth_error(detail)
        raise _CursorFailure(
            detail,
            meta=meta,
            retryable=not auth_failure and _transient_error(detail),
            status_code=401 if auth_failure and "401" in detail else (
                403 if auth_failure and "403" in detail else None
            ),
        )
    missing = _REQUIRED_CURSOR_FIELDS - outer.keys()
    if missing:
        raise _CursorFailure(
            "cursor-agent response is missing required fields: "
            + ", ".join(sorted(missing)),
            meta=meta,
        )
    usage = outer.get("usage")
    if not isinstance(usage, dict) or not {
        "inputTokens", "outputTokens"
    }.issubset(usage):
        raise _CursorFailure(
            "cursor-agent response is missing usage token fields",
            meta=meta,
        )
    if not isinstance(outer["result"], str) or not isinstance(outer["is_error"], bool):
        raise _CursorFailure(
            "cursor-agent response has invalid result or is_error fields",
            meta=meta,
        )
    if outer["is_error"]:
        if not detail:
            detail = "cursor-agent reported an error"
        auth_failure = _auth_error(detail)
        raise _CursorFailure(
            detail,
            meta=meta,
            retryable=not auth_failure and _transient_error(detail),
            status_code=401 if auth_failure and "401" in detail else (
                403 if auth_failure and "403" in detail else None
            ),
        )
    if not isinstance(outer["request_id"], str) or not outer["request_id"]:
        raise _CursorFailure(
            "cursor-agent response has no request_id",
            meta=meta,
        )

    try:
        result = outer["result"].strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```", result, re.DOTALL | re.IGNORECASE
        )
        if fenced:
            result = fenced.group(1).strip()
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            first = result.find("{")
            last = result.rfind("}")
            if first < 0 or last <= first:
                raise ValueError("Cursor result did not contain a JSON object") from None
            payload = json.loads(result[first:last + 1])
        parsed = out_model.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise _CursorFailure(
            f"{type(exc).__name__}: {exc}",
            meta=meta,
            validation_error=True,
        ) from exc
    return parsed, meta


def _call_cursor_task(
    task_name: str,
    system: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
    log_attempts: bool,
) -> tuple[BaseModel, CallMeta]:
    if log_attempts:
        db.init_db()
    model = (
        QUENYA_CURSOR_STYLIST_MODEL
        if task_name == "stylist"
        else QUENYA_CURSOR_MODEL
    )
    request_ids: list[str | None] = []
    transient_retries = 0
    validation_retries = 0
    repair_error = None
    while True:
        try:
            _check_daily_cap()
            parsed, meta = _cursor_once(
                system, model, user_content, out_model, repair_error
            )
        except _CursorFailure as exc:
            request_ids.append(exc.meta.request_id)
            if log_attempts:
                _log_attempt(
                    task_name=task_name,
                    model=model,
                    meta=exc.meta,
                    ok=False,
                    error=str(exc),
                )
            if exc.validation_error and validation_retries < _VALIDATION_RETRIES:
                validation_retries += 1
                repair_error = _safe_error(exc)
                continue
            if exc.retryable and transient_retries < _CURSOR_RETRIES:
                transient_retries += 1
                continue
            raise TaskError(
                f"{task_name} failed: {_safe_error(exc)}",
                request_ids,
                exc.status_code,
            ) from exc
        if log_attempts:
            _log_attempt(
                task_name=task_name, model=model, meta=meta, ok=True, error=None
            )
        return parsed, meta


def _call_task(
    task_name: str,
    system: str,
    model: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
    log_attempts: bool = True,
) -> tuple[BaseModel, CallMeta]:
    provider = str(QUENYA_LLM_PROVIDER).strip().lower()
    if provider == "cursor":
        return _call_cursor_task(
            task_name, system, user_content, out_model, log_attempts
        )
    if provider == "openai":
        return _call_openai_task(
            task_name, system, model, user_content, out_model, log_attempts
        )
    raise TaskError("QUENYA_LLM_PROVIDER must be 'cursor' or 'openai'")


def call_task(
    task_name: str,
    system: str,
    model: str,
    user_content: str | list[dict[str, Any]],
    out_model: type[BaseModel],
    *,
    log_attempts: bool = True,
) -> tuple[BaseModel, CallMeta]:
    with _CALL_SEMAPHORE:
        return _call_task(task_name, system, model, user_content, out_model, log_attempts)
