"""CSRF-beskyttelse, admin-login og simpel throttling af login-forsøg."""
from __future__ import annotations

import hmac
import secrets
from functools import wraps

from flask import abort, current_app, redirect, request, session, url_for

from .db import get_db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def verify_csrf() -> None:
    if request.method in SAFE_METHODS:
        return
    expected = session.get("_csrf")
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        abort(400, description="Ugyldig eller udløbet formular. Genindlæs siden og prøv igen.")


def is_admin() -> bool:
    return session.get("is_admin") is True


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def check_credentials(username: str, password: str) -> bool:
    cfg = current_app.config
    if not cfg["ADMIN_PASSWORD"]:
        return False
    user_ok = hmac.compare_digest(username.encode(), cfg["ADMIN_USERNAME"].encode())
    pass_ok = hmac.compare_digest(password.encode(), cfg["ADMIN_PASSWORD"].encode())
    return user_ok and pass_ok


def client_ip() -> str:
    return request.remote_addr or "unknown"


def login_blocked(ip: str) -> bool:
    cfg = current_app.config
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE ts < datetime('now', '-1 day')")
    n = db.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND ts > datetime('now', ?)",
        (ip, f"-{cfg['LOGIN_WINDOW_MINUTES']} minutes"),
    ).fetchone()[0]
    return n >= cfg["LOGIN_MAX_ATTEMPTS"]


def record_failed_login(ip: str) -> None:
    get_db().execute("INSERT INTO login_attempts (ip) VALUES (?)", (ip,))


def clear_failed_logins(ip: str) -> None:
    get_db().execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
