import argparse
import csv
import re
import sys
import uuid
from collections import deque
from threading import Lock
from time import monotonic
from typing import Literal, TextIO

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.requests import Request

from app import db

RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMITS: dict[str, deque[float]] = {}
_RATE_LIMIT_LOCK = Lock()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COLUMNS = (
    "id",
    "email",
    "name",
    "handle",
    "style_text",
    "wants_testing",
    "devices",
    "source",
    "user_agent",
    "created_at",
    "updated_at",
    "consent_at",
)

router = APIRouter()


class WaitlistSignup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    name: str | None = Field(default=None, max_length=80)
    handle: str | None = Field(default=None, max_length=64)
    style_text: str | None = Field(default=None, max_length=280)
    source: str | None = Field(default="marketing", max_length=64)
    wants_testing: bool = True
    devices: list[Literal["iphone", "android", "web"]] = Field(default_factory=list)
    consent: bool
    website: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if not _EMAIL_RE.fullmatch(value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("name", "handle", "style_text", "source", "website", mode="before")
    @classmethod
    def strip_optional_text(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("devices")
    @classmethod
    def deduplicate_devices(cls, value):
        return list(dict.fromkeys(value))

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value):
        if not value:
            raise ValueError("Consent is required")
        return value


def reset_rate_limit() -> None:
    with _RATE_LIMIT_LOCK:
        _RATE_LIMITS.clear()


def _check_rate_limit(request: Request) -> None:
    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = (
        forwarded_for.split(",", 1)[0].strip()
        if forwarded_for
        else request.client.host if request.client else "unknown"
    )
    now = monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _RATE_LIMIT_LOCK:
        requests = _RATE_LIMITS.setdefault(client_ip, deque())
        while requests and requests[0] <= cutoff:
            requests.popleft()
        if len(requests) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail="Too many signups from this address. Try again in a minute.",
            )
        requests.append(now)


@router.post("/api/waitlist")
def signup_waitlist(payload: WaitlistSignup, request: Request) -> dict[str, bool]:
    _check_rate_limit(request)
    if payload.website:
        return {"ok": True}

    db.init_db()
    timestamp = db.now_iso()
    devices = ",".join(payload.devices) or None
    user_agent = request.headers.get("user-agent", "")[:300] or None
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO waitlist (
                   id, email, name, handle, style_text, wants_testing, devices,
                   source, user_agent, created_at, updated_at, consent_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                   name=COALESCE(excluded.name, waitlist.name),
                   handle=COALESCE(excluded.handle, waitlist.handle),
                   style_text=COALESCE(excluded.style_text, waitlist.style_text),
                   wants_testing=excluded.wants_testing,
                   devices=COALESCE(excluded.devices, waitlist.devices),
                   source=COALESCE(excluded.source, waitlist.source),
                   user_agent=excluded.user_agent,
                   updated_at=excluded.updated_at,
                   consent_at=excluded.consent_at""",
            (
                uuid.uuid4().hex,
                payload.email,
                payload.name,
                payload.handle,
                payload.style_text,
                int(payload.wants_testing),
                devices,
                payload.source,
                user_agent,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
    return {"ok": True}


def export_waitlist(output: TextIO | None = None) -> None:
    db.init_db()
    with db.db_connection() as connection:
        rows = connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM waitlist ORDER BY created_at"
        ).fetchall()
    writer = csv.writer(output or sys.stdout)
    writer.writerow(_COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in _COLUMNS])


def _run_cli() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("export")
    args = parser.parse_args()
    if args.command == "export":
        export_waitlist()


if __name__ == "__main__":
    _run_cli()
