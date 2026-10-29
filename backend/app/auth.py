"""Authentication and roles.

Two kinds of credentials are accepted on every protected endpoint:
- service API keys from API_KEYS (for the log handler, scripts and CI) -> full access;
- user session tokens from /auth/login, /auth/signup or GitHub sign-in -> the user's role.

Roles: viewer (read-only), member (can approve, reject, revise, cancel, run, ingest, manage
repositories) and admin (member + manage users). If there are no API keys and no users yet,
auth is off so a fresh local install works; it turns on as soon as either exists.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import func, select

from .config import get_settings
from .db import SessionLocal
from .models import ROLES, User, UserSession, utcnow

log = logging.getLogger("agentops.auth")
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    kind: str  # "user" | "key" | "open"
    name: str
    role: str
    user_id: int | None = None

    @property
    def can_act(self) -> bool:
        return self.role in ("member", "admin")


# ---------------- passwords and tokens ----------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        if len(salt) != 16 or len(bytes.fromhex(hash_hex)) != 32:
            return False
        h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(h.hex(), hash_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = "aos_" + secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(UserSession(user_id=user_id, token_hash=_token_hash(token),
                           expires_at=utcnow() + timedelta(days=get_settings().session_days)))
        u = db.get(User, user_id)
        u.last_login_at = utcnow()
        db.commit()
    return token


def delete_session(token: str) -> None:
    with SessionLocal() as db:
        s = db.scalars(select(UserSession).where(UserSession.token_hash == _token_hash(token))).first()
        if s:
            db.delete(s)
            db.commit()


def users_exist() -> bool:
    with SessionLocal() as db:
        return (db.scalar(select(func.count(User.id))) or 0) > 0


def auth_enabled() -> bool:
    return bool(get_settings().api_key_list) or users_exist()


# ---------------- request authentication ----------------

def extract_token(request: Request) -> str:
    key = request.headers.get("x-api-key", "")
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key and request.url.path.endswith("/stream"):
        # EventSource can't send headers; allow a query param on SSE endpoints only.
        key = request.query_params.get("api_key", "")
    return key


def _aware(dt):
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def principal_for(token: str) -> Principal | None:
    if not token:
        return None
    if any(hmac.compare_digest(token, k) for k in get_settings().api_key_list):
        return Principal("key", "api-key", "admin")
    with SessionLocal() as db:
        s = db.scalars(select(UserSession).where(UserSession.token_hash == _token_hash(token))).first()
        if not s or _aware(s.expires_at) < utcnow():
            return None
        u = db.get(User, s.user_id)
        if u is None:
            return None
        return Principal("user", u.name, u.role if u.role in ROLES else "viewer", u.id)


def authenticate(request: Request) -> Principal:
    p = principal_for(extract_token(request))
    if p is None:
        if not auth_enabled():
            p = Principal("open", "local", "admin")
        else:
            raise HTTPException(401, "sign in or send a valid API key", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal = p
    return p


def require_api_key(request: Request) -> None:
    """Dependency for the main API: any signed-in principal can read; writes need member or admin."""
    p = authenticate(request)
    if request.method not in READ_METHODS and not p.can_act:
        raise HTTPException(403, "view-only access: ask an admin to give your account the member role")


def require_admin(request: Request) -> Principal:
    p = authenticate(request)
    if p.role != "admin":
        raise HTTPException(403, "admins only")
    return p


def actor_name(request: Request, fallback: str) -> str:
    """The name recorded for a human action: the signed-in user's name, else what the client sent."""
    p: Principal | None = getattr(request.state, "principal", None)
    return p.name if p is not None and p.kind == "user" else fallback
