"""Accounts: email/password sign-up and sign-in, GitHub sign-in (OAuth), and user management."""

from __future__ import annotations

import re
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import auth
from ..config import get_settings
from ..db import SessionLocal
from ..demo_user import DEMO_EMAIL
from ..models import ROLES, User
from ..schemas import _utc

router = APIRouter(tags=["accounts"])
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_transport: httpx.BaseTransport | None = None  # tests inject a MockTransport for GitHub


def set_transport(t: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = t


# ---------------- schemas ----------------

class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class RoleIn(BaseModel):
    role: str = Field(pattern="^(viewer|member|admin)$")


def user_out(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "name": u.name, "role": u.role,
        "github_login": u.github_login, "avatar_url": u.avatar_url,
        "has_password": bool(u.password_hash),
        "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at),
    }


def _iso(dt):
    return _utc(dt).isoformat().replace("+00:00", "Z") if dt else None


# ---------------- brute-force protection ----------------

_fail_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
MAX_FAILS, WINDOW_S = 5, 600


def _too_many(key: str) -> bool:
    now = time.time()
    with _fail_lock:
        hits = [t for t in _failures.get(key, []) if now - t < WINDOW_S]
        _failures[key] = hits
        return len(hits) >= MAX_FAILS


def _record_fail(key: str) -> None:
    with _fail_lock:
        _failures.setdefault(key, []).append(time.time())


# ---------------- helpers ----------------

def _new_user_role(db) -> str:
    """First real account is the admin. The fixed demo viewer does not count."""
    s = get_settings()
    real_accounts = db.scalar(select(func.count(User.id)).where(User.email != DEMO_EMAIL)) or 0
    if real_accounts == 0:
        return "admin"
    if s.signup_mode != "open":
        raise HTTPException(403, "sign-up is closed on this server; ask an admin to invite you")
    return s.default_role if s.default_role in ("viewer", "member") else "viewer"


def _session_response(u: User) -> dict:
    return {"token": auth.create_session(u.id), "user": user_out(u)}


# ---------------- public endpoints ----------------

@router.get("/auth/config")
def auth_config():
    """What the sign-in page should offer. No auth needed."""
    s = get_settings()
    return {
        "password": True,
        "github": bool(s.github_oauth_client_id and s.github_oauth_client_secret),
        "signup": s.signup_mode,
        "has_users": auth.users_exist(),
        "api_keys": bool(s.api_key_list),
    }


@router.post("/auth/signup", status_code=201)
def signup(body: SignupIn):
    email = body.email.strip().lower()
    if not EMAIL_RX.match(email):
        raise HTTPException(422, "enter a valid email address")
    with SessionLocal() as db:
        if db.scalars(select(User).where(User.email == email)).first():
            raise HTTPException(409, "an account with this email already exists; sign in instead")
        u = User(email=email, name=body.name.strip(), password_hash=auth.hash_password(body.password),
                 role=_new_user_role(db))
        db.add(u)
        db.commit()
    return _session_response(u)


@router.post("/auth/login")
def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else '?'}"
    if _too_many(key):
        raise HTTPException(429, "too many failed sign-in attempts; wait 10 minutes and try again")
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.email == email)).first()
    if not u or not u.password_hash or not auth.verify_password(body.password, u.password_hash):
        _record_fail(key)
        if u and not u.password_hash:
            raise HTTPException(401, "this account uses GitHub sign-in")
        raise HTTPException(401, "wrong email or password")
    return _session_response(u)


# ---------------- signed-in endpoints ----------------

@router.get("/auth/me")
def me(request: Request):
    p = auth.authenticate(request)
    if p.kind != "user":
        return {"kind": p.kind, "name": p.name, "role": p.role, "user": None}
    with SessionLocal() as db:
        return {"kind": "user", "name": p.name, "role": p.role, "user": user_out(db.get(User, p.user_id))}


@router.post("/auth/logout")
def logout(request: Request):
    token = auth.extract_token(request)
    if token:
        auth.delete_session(token)
    return {"ok": True}


@router.get("/users")
def list_users(_: auth.Principal = Depends(auth.require_admin)):
    with SessionLocal() as db:
        return [user_out(u) for u in db.scalars(select(User).order_by(User.id)).all()]


@router.patch("/users/{user_id}")
def set_role(user_id: int, body: RoleIn, admin: auth.Principal = Depends(auth.require_admin)):
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        if u.email == DEMO_EMAIL and body.role != "viewer":
            raise HTTPException(409, "the demo account is limited to the viewer role")
        if u.role == "admin" and body.role != "admin":
            admins = db.scalar(select(func.count(User.id)).where(User.role == "admin")) or 0
            if admins <= 1:
                raise HTTPException(409, "there must be at least one admin")
        u.role = body.role
        db.commit()
        return user_out(u)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: auth.Principal = Depends(auth.require_admin)):
    if admin.user_id == user_id:
        raise HTTPException(409, "you can't remove your own account")
    from ..models import UserSession

    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        for s in db.scalars(select(UserSession).where(UserSession.user_id == user_id)).all():
            db.delete(s)
        db.delete(u)
        db.commit()


# ---------------- GitHub sign-in (OAuth web flow) ----------------

_states: dict[str, float] = {}
_state_lock = threading.Lock()


def _back(fragment: dict) -> RedirectResponse:
    base = get_settings().frontend_url.rstrip("/")
    return RedirectResponse(f"{base}/auth/callback#{urlencode(fragment)}", status_code=302)


@router.get("/auth/github/start")
def github_start():
    s = get_settings()
    if not (s.github_oauth_client_id and s.github_oauth_client_secret):
        raise HTTPException(404, "GitHub sign-in is not configured on this server")
    state = secrets.token_urlsafe(24)
    with _state_lock:
        now = time.time()
        for k in [k for k, t in _states.items() if now - t > 600]:
            _states.pop(k, None)
        _states[state] = now
    q = urlencode({"client_id": s.github_oauth_client_id, "scope": "read:user user:email", "state": state,
                   "allow_signup": "true"})
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{q}", status_code=302)


@router.get("/auth/github/callback")
def github_callback(code: str = "", state: str = "", error: str = ""):
    s = get_settings()
    if error:
        return _back({"error": f"GitHub sign-in was cancelled ({error})"})
    with _state_lock:
        started = _states.pop(state, None)
    if not code or started is None or time.time() - started > 600:
        return _back({"error": "sign-in link expired; try again"})
    try:
        with httpx.Client(timeout=15, transport=_transport) as http:
            tok = http.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"},
                            data={"client_id": s.github_oauth_client_id, "client_secret": s.github_oauth_client_secret,
                                  "code": code}).json().get("access_token")
            if not tok:
                return _back({"error": "GitHub did not return an access token"})
            gh = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
            profile = http.get("https://api.github.com/user", headers=gh).json()
            # A public profile email is not proof of ownership. Only a verified email
            # from the authenticated email endpoint may link an existing account.
            email_response = http.get("https://api.github.com/user/emails", headers=gh)
            email_response.raise_for_status()
            emails = email_response.json()
            verified = [e for e in emails if isinstance(e, dict) and e.get("verified") and e.get("email")]
            primary = next((e for e in verified if e.get("primary")), None)
            email = (primary or (verified[0] if verified else {})).get("email", "").strip().lower()
    except httpx.HTTPError as e:
        return _back({"error": f"could not reach GitHub: {e}"})
    if not profile.get("id"):
        return _back({"error": "GitHub sign-in failed"})
    if not email:
        return _back({"error": "GitHub sign-in requires a verified email address"})

    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.github_id == profile["id"])).first() \
            or db.scalars(select(User).where(User.email == email)).first()
        if u is not None and u.github_id not in (None, profile["id"]):
            return _back({"error": "this email is already linked to a different GitHub account"})
        if u is None:
            try:
                role = _new_user_role(db)
            except HTTPException as e:
                return _back({"error": e.detail})
            u = User(email=email, name=profile.get("name") or profile["login"], role=role)
            db.add(u)
        u.github_id = profile["id"]
        u.github_login = profile.get("login", "")
        u.avatar_url = profile.get("avatar_url", "")
        db.commit()
        uid = u.id
    return _back({"token": auth.create_session(uid)})
