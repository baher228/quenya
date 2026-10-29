import argparse
import csv
import logging
import re
import smtplib
import sys
import uuid
from collections import deque
from threading import Lock
from time import monotonic
from typing import Literal, TextIO

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.requests import Request

from app import accounts, db, mailer
from app.accounts import AdminUser
from app.config import QUENYA_APP_URL

RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMITS: dict[str, deque[float]] = {}
_RATE_LIMIT_LOCK = Lock()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INVITE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
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
    "status",
    "user_id",
    "reviewed_at",
)

router = APIRouter()
logger = logging.getLogger(__name__)


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


class InviteEmailRequest(BaseModel):
    invite_url: str


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


@router.get("/api/admin/waitlist")
def admin_waitlist(
    _user: AdminUser,
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
) -> list[dict]:
    db.init_db()
    columns = (
        "id", "email", "name", "handle", "style_text", "wants_testing",
        "devices", "source", "created_at", "status", "reviewed_at", "user_id",
    )
    query = f"SELECT {', '.join(columns)} FROM waitlist"
    values: tuple[str, ...] = ()
    if status != "all":
        query += " WHERE status=?"
        values = (status,)
    query += " ORDER BY created_at DESC, id DESC"
    with db.db_connection() as connection:
        rows = connection.execute(query, values).fetchall()
    return [dict(row) for row in rows]


@router.post("/api/admin/waitlist/{waitlist_id}/approve")
def approve_waitlist(waitlist_id: str, _user: AdminUser) -> dict:
    db.init_db()
    reviewed_at = db.now_iso()
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT email, name, status, reviewed_at FROM waitlist WHERE id=?",
            (waitlist_id,),
        ).fetchone()
        cursor = connection.execute(
            """UPDATE waitlist SET status='approved', reviewed_at=?
               WHERE id=? AND status<>'approved'""",
            (reviewed_at, waitlist_id),
        )
        if cursor.rowcount == 0:
            exists = connection.execute(
                "SELECT 1 FROM waitlist WHERE id=?", (waitlist_id,)
            ).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Waitlist entry not found")
            raise HTTPException(
                status_code=409, detail="Waitlist entry already approved"
            )
        if not row:
            row = connection.execute(
                "SELECT email, name, status, reviewed_at FROM waitlist WHERE id=?",
                (waitlist_id,),
            ).fetchone()

    try:
        user, token = accounts.create_user(row["name"] or row["email"], "tester")
    except Exception:
        with db.db_connection() as connection:
            connection.execute(
                """UPDATE waitlist SET status=?, reviewed_at=?
                   WHERE id=? AND status='approved' AND user_id IS NULL""",
                (row["status"], row["reviewed_at"], waitlist_id),
            )
        raise
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE waitlist SET user_id=? WHERE id=? AND status='approved'",
            (user["id"], waitlist_id),
        )
    return {
        "invite_url": accounts.invite_url(token),
        "user": {key: user[key] for key in ("id", "name", "role")},
    }


@router.post("/api/admin/waitlist/{waitlist_id}/reject")
def reject_waitlist(waitlist_id: str, _user: AdminUser) -> dict[str, bool]:
    db.init_db()
    with db.db_connection() as connection:
        cursor = connection.execute(
            """UPDATE waitlist SET status='rejected', reviewed_at=?
               WHERE id=?""",
            (db.now_iso(), waitlist_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")
    return {"ok": True}


@router.post("/api/admin/waitlist/{waitlist_id}/email-invite")
def email_waitlist_invite(
    waitlist_id: str,
    payload: InviteEmailRequest,
    _user: AdminUser,
) -> dict[str, str | bool]:
    db.init_db()
    with db.db_connection() as connection:
        entry = connection.execute(
            "SELECT email, name, status, user_id FROM waitlist WHERE id=?",
            (waitlist_id,),
        ).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")
    if entry["status"] != "approved" or not entry["user_id"]:
        raise HTTPException(
            status_code=409,
            detail="Waitlist entry is not approved",
        )

    prefix = f"{QUENYA_APP_URL}/?invite="
    if not payload.invite_url.startswith(prefix):
        raise HTTPException(status_code=400, detail="Invalid invite link")
    token = payload.invite_url[len(prefix):]
    if not _INVITE_TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=400, detail="Invalid invite link")

    with db.db_connection() as connection:
        user = connection.execute(
            "SELECT invite_hash, revoked_at FROM users WHERE id=?",
            (entry["user_id"],),
        ).fetchone()
    if (
        not user
        or user["revoked_at"] is not None
        or user["invite_hash"] != accounts._hash(token)
    ):
        raise HTTPException(
            status_code=400,
            detail="Invite link doesn't belong to this tester",
        )

    try:
        mailer.send_email(
            entry["email"],
            *mailer.invite_email(entry["name"], payload.invite_url),
        )
    except mailer.MailNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Email isn't set up on the server",
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception(
            "Failed to send invite email for waitlist entry %s",
            waitlist_id,
        )
        raise HTTPException(
            status_code=502,
            detail="Couldn't send the email",
        ) from exc
    return {"ok": True, "sent_to": entry["email"]}


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
