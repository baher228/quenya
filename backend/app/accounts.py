"""Invite-link users and bearer sessions."""
import argparse
import base64
import getpass
import hashlib
import hmac
import secrets
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app import db, rate_limit
from app.config import QUENYA_APP_URL

_user_context: ContextVar[dict[str, Any] | None] = ContextVar("user_context", default=None)
_in_request: ContextVar[bool] = ContextVar("in_request", default=False)
router = APIRouter()
APP_ONLY_DETAIL = "Quenya is on iPhone only. Open your invite in the Quenya app."
_LOGIN_RATE_WINDOW_S = 15 * 60
_LOGIN_RATE_LIMIT = 5


def is_browser(user_agent: str | None) -> bool:
    return isinstance(user_agent, str) and "mozilla/" in user_agent.lower()


class TesterInviteNotAllowed(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return (
        f"scrypt$16384$8$1${base64.b64encode(salt).decode('ascii')}"
        f"${base64.b64encode(digest).decode('ascii')}"
    )


_DUMMY_PASSWORD_HASH = _password_hash("quenya-dummy-admin-password")


def set_password(user_id: str, password: str) -> None:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    password_hash = _password_hash(password)
    db.init_db()
    with db.db_connection() as connection:
        result = connection.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (password_hash, user_id),
        )
        if result.rowcount == 0:
            raise ValueError("user not found")


def verify_password(stored: str | None, password: str) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_text, digest_text = stored.split("$")
        if (scheme, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def _login_is_limited(ip: str, name: str) -> bool:
    return rate_limit.is_limited(
        f"admin-login-ip:{ip}",
        limit=_LOGIN_RATE_LIMIT,
        window_seconds=_LOGIN_RATE_WINDOW_S,
    ) or rate_limit.is_limited(
        f"admin-login-name:{name}",
        limit=_LOGIN_RATE_LIMIT,
        window_seconds=_LOGIN_RATE_WINDOW_S,
    )


def _record_login_failure(ip: str, name: str) -> None:
    rate_limit.hit(f"admin-login-ip:{ip}")
    rate_limit.hit(f"admin-login-name:{name}")


def _clear_name_failures(name: str) -> None:
    rate_limit.reset_bucket(f"admin-login-name:{name}")


def reset_admin_login_rate_limit() -> None:
    rate_limit.reset_bucket("admin-login-ip:")
    rate_limit.reset_bucket("admin-login-name:")



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
            """INSERT INTO users
               (id,name,role,invite_hash,created_at,revoked_at,password_hash)
               VALUES (?,?,?,?,?,?,NULL)""",
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


def _new_session(user_id: str) -> str:
    db.init_db()
    session = secrets.token_urlsafe(32)
    now = db.now_iso()
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO auth_sessions
               (token_hash,user_id,created_at,last_seen_at,revoked_at)
               VALUES (?,?,?,?,NULL)""",
            (_hash(session), user_id, now, now),
        )
    return session


def redeem_invite(token: str, *, allow_tester: bool = True) -> str:
    db.init_db()
    with db.db_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE invite_hash=? AND revoked_at IS NULL", (_hash(token),)
        ).fetchone()
        if not row:
            raise ValueError("invalid invite")
        if row["role"] == "tester" and not allow_tester:
            raise TesterInviteNotAllowed("tester invites can only be opened in the app")
        user_id = row["id"]
    return _new_session(user_id)


def _client_ip(request: Request) -> str:
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded_ip = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if forwarded_ip:
        return forwarded_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


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
    return f"{QUENYA_APP_URL}/open?invite={token}"


async def current_user(
    authorization: str | None = Header(default=None),
    user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    user = await run_in_threadpool(user_for_session, token)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with your invite link")
    if user["role"] != "admin" and is_browser(user_agent):
        raise HTTPException(status_code=403, detail=APP_ONLY_DETAIL)
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


def in_request() -> bool:
    return _in_request.get()


def clear_context() -> None:
    _user_context.set(None)


@router.post("/api/auth/invite")
def redeem(
    body: dict[str, str],
    user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> dict[str, Any]:
    try:
        token = redeem_invite(body["token"], allow_tester=not is_browser(user_agent))
    except TesterInviteNotAllowed as exc:
        raise HTTPException(status_code=403, detail=APP_ONLY_DETAIL) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid invite") from exc
    user = user_for_session(token)
    return {"token": token, "user": _public(user or {})}


@router.post("/api/auth/admin-login")
def admin_login(body: dict[str, str], request: Request) -> dict[str, Any]:
    name = body.get("name", "").strip()
    password = body.get("password", "")
    normalized_name = name.casefold()
    ip = _client_ip(request)
    if _login_is_limited(ip, normalized_name):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts, try again later",
        )

    db.init_db()
    with db.db_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM users
               WHERE role='admin' AND revoked_at IS NULL
                 AND password_hash IS NOT NULL"""
        ).fetchall()
    matching_users = [
        dict(row)
        for row in rows
        if row["name"].strip().casefold() == normalized_name
    ]
    user = matching_users[0] if len(matching_users) == 1 else None
    valid_password = verify_password(
        user["password_hash"] if user else _DUMMY_PASSWORD_HASH,
        password,
    )
    if not user or not valid_password:
        _record_login_failure(ip, normalized_name)
        raise HTTPException(status_code=401, detail="Wrong name or password")

    _clear_name_failures(normalized_name)
    token = _new_session(user["id"])
    return {"token": token, "user": _public(user)}


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
    count = db.profile_count("user", user["id"])
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
        styles = connection.execute(
            """WITH swipe_counts AS (
                   SELECT profile_id, COUNT(*) AS total FROM swipes GROUP BY profile_id
               )
               SELECT p.owner_id, p.id, p.label, COALESCE(s.total, 0) AS swipe_count
               FROM profiles p LEFT JOIN swipe_counts s ON s.profile_id=p.id
               WHERE p.kind='user' AND p.owner_id IN (SELECT id FROM users)
               ORDER BY COALESCE(p.updated_at, p.created_at) DESC, p.created_at DESC"""
        ).fetchall()
    styles_by_owner: dict[str, list[dict[str, Any]]] = {}
    swipes_by_owner: dict[str, int] = {}
    for style in styles:
        owner_id = style["owner_id"]
        styles_by_owner.setdefault(owner_id, []).append({
            "id": style["id"], "label": style["label"],
        })
        swipes_by_owner[owner_id] = swipes_by_owner.get(owner_id, 0) + style["swipe_count"]
    result = []
    for row in rows:
        user = dict(row)
        user["styles"] = styles_by_owner.get(user["id"], [])
        user["swipe_count"] = swipes_by_owner.get(user["id"], 0)
        result.append({key: user.get(key) for key in (
            "id", "name", "role", "created_at", "revoked_at", "last_seen_at",
            "styles", "swipe_count",
        )})
    return result


@router.get("/api/admin/users")
def admin_users(_user: AdminUser) -> list[dict[str, Any]]:
    return _admin_users()


@router.post("/api/admin/password")
def admin_change_password(
    body: dict[str, str],
    user: AdminUser,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    if user.get("password_hash") and not verify_password(
        user["password_hash"],
        body.get("current_password", ""),
    ):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    new_password = body.get("new_password", "")
    if len(new_password) < 12:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 12 characters",
        )
    set_password(user["id"], new_password)
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with db.db_connection() as connection:
        connection.execute(
            """UPDATE auth_sessions SET revoked_at=?
               WHERE user_id=? AND token_hash<>?""",
            (db.now_iso(), user["id"], _hash(token)),
        )
    return {"ok": True}


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
    set_password_command = sub.add_parser("set-password")
    set_password_command.add_argument("--name", required=True)
    set_password_command.add_argument("--generate", action="store_true")
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
    elif args.command == "set-password":
        with db.db_connection() as connection:
            users = connection.execute(
                "SELECT id FROM users WHERE role='admin' AND name=?",
                (args.name,),
            ).fetchall()
        if len(users) != 1:
            parser.error(f"expected exactly one admin user named {args.name!r}")
        password = (
            secrets.token_urlsafe(15)
            if args.generate
            else getpass.getpass("Password: ")
        )
        try:
            set_password(users[0]["id"], password)
        except ValueError as exc:
            parser.error(str(exc))
        if args.generate:
            print(password)
        else:
            print("Password updated.")


if __name__ == "__main__":
    main()
