"""Invite-link users and bearer sessions."""
import argparse
import hashlib
import secrets
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app import db
from app.config import QUENYA_APP_URL

_user_context: ContextVar[dict[str, Any] | None] = ContextVar("user_context", default=None)
router = APIRouter()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ("id", "name", "role")}


def create_user(name: str, role: str = "tester") -> tuple[dict[str, Any], str]:
    if role not in {"tester", "admin"}:
        raise ValueError("role must be tester or admin")
    db.init_db()
    token = secrets.token_urlsafe(32)
    user = {
        "id": uuid.uuid4().hex,
        "name": name,
        "role": role,
        "invite_hash": _hash(token),
        "created_at": db.now_iso(),
        "revoked_at": None,
    }
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO users (id,name,role,invite_hash,created_at,revoked_at)
               VALUES (?,?,?,?,?,?)""",
            tuple(user.values()),
        )
    return user, token


def rotate_invite(user_id: str) -> str:
    db.init_db()
    token = secrets.token_urlsafe(32)
    with db.db_connection() as connection:
        row = connection.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("user not found")
        connection.execute("UPDATE users SET invite_hash=? WHERE id=?", (_hash(token), user_id))
    return token


def redeem_invite(token: str) -> str:
    db.init_db()
    token_hash = _hash(token)
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE invite_hash=? AND revoked_at IS NULL", (token_hash,)
        ).fetchone()
        if not row:
            raise ValueError("invalid invite")
        session = secrets.token_urlsafe(32)
        connection.execute(
            """INSERT INTO auth_sessions
               (token_hash,user_id,created_at,last_seen_at,revoked_at)
               VALUES (?,?,?,?,NULL)""",
            (_hash(session), row["id"], db.now_iso(), db.now_iso()),
        )
    return session


def user_for_session(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    db.init_db()
    with db.db_connection() as connection:
        row = connection.execute(
            """SELECT u.*, s.last_seen_at AS session_last_seen_at
               FROM auth_sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.revoked_at IS NULL AND u.revoked_at IS NULL""",
            (_hash(token),),
        ).fetchone()
        if not row:
            return None
        user = dict(row)
        last = user.pop("session_last_seen_at", None)
        now = datetime.now(UTC)
        if not last:
            touch = True
        else:
            try:
                touch = (now - datetime.fromisoformat(last)).total_seconds() >= 60
            except ValueError:
                touch = True
        if touch:
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?",
                (now.isoformat(), _hash(token)),
            )
    return user


def revoke_user(user_id: str) -> None:
    db.init_db()
    with db.db_connection() as connection:
        connection.execute("UPDATE users SET revoked_at=? WHERE id=?", (db.now_iso(), user_id))
        connection.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE user_id=?",
            (db.now_iso(), user_id),
        )


def invite_url(token: str) -> str:
    return f"{QUENYA_APP_URL}/?invite={token}"


async def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    user = user_for_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with your invite link")
    _user_context.set(user)
    return user


def require_admin(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]
AdminUser = Annotated[dict[str, Any], Depends(require_admin)]


def context_user() -> dict[str, Any] | None:
    return _user_context.get()


def clear_context() -> None:
    _user_context.set(None)


@router.post("/api/auth/invite")
def redeem(body: dict[str, str]) -> dict[str, Any]:
    try:
        token = redeem_invite(body["token"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid invite") from exc
    user = user_for_session(token)
    return {"token": token, "user": _public(user or {})}


@router.post("/api/auth/logout")
def logout(
    _user: CurrentUser,
    authorization: str | None = Header(default=None),
):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with db.db_connection() as connection:
        connection.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
            (db.now_iso(), _hash(token)),
        )
    clear_context()
    return {"ok": True}


@router.get("/api/me")
def me(user: CurrentUser) -> dict[str, Any]:
    count = len(db.profile_boards("user", user["id"]))
    return {
        **_public(user),
        "max_styles": 1 if user["role"] == "tester" else None,
        "style_count": count,
    }


def _admin_users() -> list[dict[str, Any]]:
    db.init_db()
    with db.db_connection() as connection:
        rows = connection.execute(
            """SELECT u.*, MAX(s.last_seen_at) AS last_seen_at
               FROM users u LEFT JOIN auth_sessions s ON s.user_id=u.id
               GROUP BY u.id ORDER BY u.created_at"""
        ).fetchall()
    result = []
    for row in rows:
        user = dict(row)
        boards = db.profile_boards("user", user["id"])
        user["styles"] = [{"id": b["id"], "label": b["label"]} for b in boards]
        user["swipe_count"] = sum(b["counts"]["swipes"] for b in boards)
        result.append({key: user.get(key) for key in (
            "id", "name", "role", "created_at", "revoked_at", "last_seen_at",
            "styles", "swipe_count",
        )})
    return result


@router.get("/api/admin/users")
def admin_users(_user: AdminUser) -> list[dict[str, Any]]:
    return _admin_users()


@router.post("/api/admin/invites")
def admin_invite(body: dict[str, str], _user: AdminUser):
    user, token = create_user(body["name"], body.get("role", "tester"))
    return {"user": _public(user), "invite_url": invite_url(token)}


@router.post("/api/admin/users/{user_id}/rotate-invite")
def admin_rotate(user_id: str, _user: AdminUser):
    try:
        return {"invite_url": invite_url(rotate_invite(user_id))}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc


@router.post("/api/admin/users/{user_id}/revoke")
def admin_revoke(user_id: str, _user: AdminUser):
    revoke_user(user_id)
    return {"ok": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap-admin")
    bootstrap.add_argument("--name", required=True)
    invite = sub.add_parser("invite")
    invite.add_argument("--name", required=True)
    invite.add_argument("--admin", action="store_true")
    sub.add_parser("list")
    rotate = sub.add_parser("rotate")
    rotate.add_argument("--id", required=True)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--id", required=True)
    args = parser.parse_args()
    db.init_db()
    if args.command == "list":
        print(_admin_users())
        return
    if args.command == "bootstrap-admin":
        with db.db_connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE role='admin' ORDER BY created_at LIMIT 1"
            ).fetchone()
        if row:
            user = dict(row)
            token = rotate_invite(user["id"])
        else:
            user, token = create_user(args.name, "admin")
        with db.db_connection() as connection:
            connection.execute(
                "UPDATE profiles SET owner_id=? WHERE owner_id IS NULL AND kind='user'",
                (user["id"],),
            )
            for table in ("routes", "uploads"):
                connection.execute(
                    f"UPDATE {table} SET owner_id=? WHERE owner_id IS NULL",
                    (user["id"],),
                )
        print(invite_url(token))
    elif args.command == "invite":
        user, token = create_user(args.name, "admin" if args.admin else "tester")
        print(invite_url(token))
    elif args.command == "rotate":
        print(invite_url(rotate_invite(args.id)))
    elif args.command == "revoke":
        revoke_user(args.id)


if __name__ == "__main__":
    main()
