"""Google OAuth flow + lightweight local token/session store.

This is a pragmatic scaffold for local/dev use. For production multi-user apps,
replace file-backed storage with your database + encryption/KMS.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from urllib.parse import quote_plus, urlparse
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Repo root: backend/auth/google_oauth.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_dotenv_override = (
    os.environ.get("JARVIS_DOTENV_PATH") or os.environ.get("ADA_DOTENV_PATH") or ""
).strip()
if _dotenv_override:
    load_dotenv(Path(_dotenv_override).expanduser(), encoding="utf-8")
else:
    try:
        from config import data_root

        load_dotenv(data_root() / ".env", encoding="utf-8")
        if data_root() != _REPO_ROOT:
            load_dotenv(_REPO_ROOT / ".env", encoding="utf-8", override=False)
    except Exception:
        load_dotenv(_REPO_ROOT / ".env", encoding="utf-8")

_DEFAULT_STORE_PATH = _REPO_ROOT / ".secrets" / "google-oauth-store.json"
_LOCK = threading.Lock()
_PENDING_TTL_SEC = 10 * 60
_SESSION_TTL_SEC = 30 * 24 * 60 * 60


def _now() -> int:
    return int(time.time())


def _is_packaged() -> bool:
    if getattr(__import__("sys"), "frozen", False):
        return True
    for key in ("JARVIS_PACKAGED", "ADA_PACKAGED"):
        if os.environ.get(key, "").strip().lower() in ("1", "true", "yes", "on"):
            return True
    return False


def _store_path() -> Path:
    raw = (os.environ.get("GOOGLE_OAUTH_STORE_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if _is_packaged():
        try:
            from config import data_root

            return data_root() / ".secrets" / "google-oauth-store.json"
        except Exception:
            appdata = (
                os.environ.get("JARVIS_DATA_DIR")
                or os.environ.get("ADA_DATA_DIR")
                or os.environ.get("APPDATA")
            )
            if appdata:
                return Path(appdata) / ".secrets" / "google-oauth-store.json"
    return _DEFAULT_STORE_PATH


def _cookie_secure() -> bool:
    env = (os.environ.get("GOOGLE_OAUTH_COOKIE_SECURE") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return _redirect_uri().lower().startswith("https://")


def _frontend_base_url() -> str:
    env = (
        os.environ.get("GOOGLE_OAUTH_FRONTEND_URL")
        or os.environ.get("FRONTEND_URL")
        or ""
    ).strip().rstrip("/")
    if env:
        return env
    if _is_packaged():
        return "https://tauri.localhost"
    return "http://localhost:5173"


def _redirect_uri() -> str:
    env = (os.environ.get("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
    if env:
        return env
    if _is_packaged():
        return "http://127.0.0.1:8000/auth/google/callback"
    return "http://localhost:5173/api/auth/google/callback"


def _client_id() -> str:
    return (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip().strip('"').strip("'")


def _client_secret() -> str:
    return (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip().strip('"').strip("'")


def _scopes() -> str:
    raw = (os.environ.get("GOOGLE_OAUTH_SCOPES") or "").strip()
    if raw:
        return raw
    return (
        "openid email profile "
        "https://www.googleapis.com/auth/calendar "
        "https://www.googleapis.com/auth/gmail.modify "
        "https://www.googleapis.com/auth/gmail.send"
    )


def _safe_next_path(next_path: str | None) -> str:
    """Allow only a same-origin relative path: /foo, not //evil, /\\evil, or CRLF."""
    raw = (next_path or "/").strip() or "/"
    if "\r" in raw or "\n" in raw or "\\" in raw:
        return "/"
    if not raw.startswith("/") or raw.startswith("//"):
        return "/"
    if "://" in raw:
        return "/"
    return raw


def oauth_is_configured() -> bool:
    return bool(_client_id() and _client_secret())


def oauth_missing_config_fields() -> list[str]:
    missing = []
    if not _client_id():
        missing.append("GOOGLE_OAUTH_CLIENT_ID")
    if not _client_secret():
        missing.append("GOOGLE_OAUTH_CLIENT_SECRET")
    return missing


def oauth_redirect_uri() -> str:
    """Exact redirect_uri sent to Google; must match Authorized redirect URIs in GCP (character-for-character)."""
    return _redirect_uri()


def oauth_client_id_hint() -> str | None:
    """Short fingerprint so you can confirm .env matches the OAuth client you edited in GCP (client id is public)."""
    cid = _client_id()
    if not cid:
        return None
    if len(cid) <= 24:
        return f"{cid[:8]}…"
    return f"{cid[:12]}…{cid[-10:]}"


def oauth_suggested_javascript_origin() -> str | None:
    """Origin derived from redirect URI; add under Authorized JavaScript origins for Web clients when Google asks."""
    u = _redirect_uri()
    try:
        p = urlparse(u)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return None


def _empty_store() -> dict[str, Any]:
    return {"pending": {}, "sessions": {}, "users": {}}


def _is_encrypted_store(data: dict[str, Any]) -> bool:
    return "blob" in data and str(data.get("v") or "") in ("2", "2.0")


def _load_store() -> dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return _empty_store()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        if _is_encrypted_store(data):
            from auth.secret_box import open_json

            opened = open_json(data)
            if not isinstance(opened, dict):
                return _empty_store()
            data = opened
        data.setdefault("pending", {})
        data.setdefault("sessions", {})
        data.setdefault("users", {})
        return data
    except Exception:
        # Do not overwrite a corrupt-but-maybe-recoverable file with {}.
        return _empty_store()


def _save_store(data: dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    from auth.secret_box import seal_json

    payload = seal_json(data)
    if not isinstance(payload, dict) or "blob" not in payload:
        raise RuntimeError("OAuth store encryption failed; refusing to write plaintext tokens.")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _cleanup_expired(data: dict[str, Any]) -> None:
    now = _now()
    pending = data.get("pending") or {}
    sessions = data.get("sessions") or {}
    for key, row in list(pending.items()):
        ts = int((row or {}).get("created_at") or 0)
        if now - ts > _PENDING_TTL_SEC:
            pending.pop(key, None)
    for sid, row in list(sessions.items()):
        ts = int((row or {}).get("last_used") or (row or {}).get("created_at") or 0)
        if now - ts > _SESSION_TTL_SEC:
            sessions.pop(sid, None)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def create_login_url(next_path: str | None = None) -> str:
    if not oauth_is_configured():
        missing = ", ".join(oauth_missing_config_fields())
        raise ValueError(f"Google OAuth is not configured. Missing: {missing}")

    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        data["pending"][state] = {
            "code_verifier": verifier,
            "created_at": _now(),
            "next_path": _safe_next_path(next_path),
        }
        _save_store(data)

    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": _scopes(),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return str(httpx.URL("https://accounts.google.com/o/oauth2/v2/auth", params=params))


def exchange_code_and_create_session(code: str, state: str) -> tuple[str, str]:
    if not oauth_is_configured():
        missing = ", ".join(oauth_missing_config_fields())
        raise ValueError(f"Google OAuth is not configured. Missing: {missing}")

    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        pending = (data.get("pending") or {}).pop(state, None)
        if pending:
            pending = dict(pending)
            _save_store(data)
    if not pending:
        raise ValueError("Invalid or expired OAuth state.")

    verifier = str(pending.get("code_verifier") or "")
    if not verifier:
        raise ValueError("OAuth verifier missing for this state.")

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=20.0,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("Google token response did not include access_token.")

    userinfo_resp = httpx.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20.0,
    )
    userinfo_resp.raise_for_status()
    profile = userinfo_resp.json()
    sub = str(profile.get("sub") or "").strip()
    if not sub:
        raise ValueError("Google user profile did not include subject id.")

    sid = secrets.token_urlsafe(32)
    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        users = data.setdefault("users", {})
        sessions = data.setdefault("sessions", {})
        current = users.get(sub) or {}
        old_refresh = ((current.get("tokens") or {}).get("refresh_token") or "").strip()
        exp_at = None
        ei = token_data.get("expires_in")
        if ei is not None:
            try:
                exp_at = _now() + int(ei)
            except (TypeError, ValueError):
                pass
        users[sub] = {
            "sub": sub,
            "email": profile.get("email"),
            "name": profile.get("name"),
            "picture": profile.get("picture"),
            "updated_at": _now(),
            "tokens": {
                "access_token": token_data.get("access_token"),
                "refresh_token": token_data.get("refresh_token") or old_refresh or None,
                "scope": token_data.get("scope"),
                "token_type": token_data.get("token_type"),
                "expires_in": token_data.get("expires_in"),
                "expires_at": exp_at if exp_at is not None else _now() + 3500,
            },
        }
        sessions[sid] = {"sub": sub, "created_at": _now(), "last_used": _now()}
        (data.get("pending") or {}).pop(state, None)
        _save_store(data)

    next_path = _safe_next_path(str(pending.get("next_path") or "/"))
    return sid, next_path


def _access_token_stale(tokens: dict[str, Any]) -> bool:
    exp = tokens.get("expires_at")
    if exp is None:
        return True
    try:
        return _now() >= int(exp) - 120
    except (TypeError, ValueError):
        return True


def _refresh_tokens_http(refresh_token: str) -> dict[str, Any]:
    if not oauth_is_configured():
        raise ValueError("OAuth client is not configured on the server.")
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_tokens_for_user(data: dict[str, Any], sub: str) -> str:
    """Refresh Google access token; mutates data['users'][sub]['tokens']. Returns new access_token."""
    users = data.setdefault("users", {})
    user = users.get(sub) or {}
    tok = dict(user.get("tokens") or {})
    rt = (tok.get("refresh_token") or "").strip()
    if not rt:
        raise ValueError("Missing refresh token. Sign in with Google again.")
    body = _refresh_tokens_http(rt)
    at = (body.get("access_token") or "").strip()
    if not at:
        raise ValueError("Token refresh did not return access_token.")
    exp_at = None
    if body.get("expires_in") is not None:
        try:
            exp_at = _now() + int(body["expires_in"])
        except (TypeError, ValueError):
            pass
    new_refresh = (body.get("refresh_token") or "").strip() or rt
    user["tokens"] = {
        **tok,
        "access_token": at,
        "refresh_token": new_refresh,
        "expires_in": body.get("expires_in"),
        "expires_at": exp_at if exp_at is not None else _now() + 3500,
        "scope": body.get("scope") or tok.get("scope"),
        "token_type": body.get("token_type") or tok.get("token_type"),
    }
    user["updated_at"] = _now()
    users[sub] = user
    return at


_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_REFRESH_META = threading.Lock()


def _refresh_lock_for(sub: str) -> threading.Lock:
    with _REFRESH_META:
        lock = _REFRESH_LOCKS.get(sub)
        if lock is None:
            lock = threading.Lock()
            _REFRESH_LOCKS[sub] = lock
        return lock


def get_valid_access_token_for_session(session_id: str | None) -> tuple[str | None, str | None]:
    """
    Return (access_token, error_message). Refreshes the access token when stale or missing
    (if a refresh_token is stored).
    """
    if not session_id:
        return None, "Not signed in."
    refresh_needed = False
    refresh_sub = ""
    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        sessions = data.get("sessions") or {}
        users = data.get("users") or {}
        row = sessions.get(session_id)
        if not row:
            _save_store(data)
            return None, "Session expired. Sign in again."
        row["last_used"] = _now()
        sub = row.get("sub")
        user = users.get(sub) or {}
        tok = user.get("tokens") or {}
        access = (tok.get("access_token") or "").strip()
        if (not access or _access_token_stale(tok)) and (tok.get("refresh_token") or "").strip():
            refresh_needed = True
            refresh_sub = str(sub)
        else:
            _save_store(data)
    if refresh_needed:
        rlock = _refresh_lock_for(refresh_sub)
        with rlock:
            with _LOCK:
                data = _load_store()
                tok = ((data.get("users") or {}).get(refresh_sub) or {}).get("tokens") or {}
                if tok and not _access_token_stale(tok) and (tok.get("access_token") or "").strip():
                    access = (tok.get("access_token") or "").strip()
                    return access, None
                rt = (tok.get("refresh_token") or "").strip()
            if not rt:
                return None, "Missing refresh token. Sign in with Google again."
            try:
                body = _refresh_tokens_http(rt)
                at = (body.get("access_token") or "").strip()
                if not at:
                    raise ValueError("Token refresh did not return access_token.")
            except Exception as e:
                return None, str(e)
            with _LOCK:
                data = _load_store()
                users = data.setdefault("users", {})
                user = users.get(refresh_sub) or {}
                tok = dict(user.get("tokens") or {})
                exp_at = None
                if body.get("expires_in") is not None:
                    try:
                        exp_at = _now() + int(body["expires_in"])
                    except (TypeError, ValueError):
                        pass
                user["tokens"] = {
                    **tok,
                    "access_token": at,
                    "refresh_token": (body.get("refresh_token") or "").strip() or rt,
                    "expires_in": body.get("expires_in"),
                    "expires_at": exp_at if exp_at is not None else _now() + 3500,
                    "scope": body.get("scope") or tok.get("scope"),
                    "token_type": body.get("token_type") or tok.get("token_type"),
                }
                user["updated_at"] = _now()
                users[refresh_sub] = user
                _save_store(data)
                access = at
    if not access:
        return None, "No access token. Sign in with Google again."
    return access, None


def google_status_by_session(session_id: str | None) -> dict[str, Any]:
    if not session_id:
        return {"connected": False, "configured": oauth_is_configured(), "user": None}
    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        sessions = data.get("sessions") or {}
        users = data.get("users") or {}
        row = sessions.get(session_id)
        if not row:
            _save_store(data)
            return {"connected": False, "configured": oauth_is_configured(), "user": None}
        user = users.get(row.get("sub")) or {}
        _save_store(data)
    if not user:
        return {"connected": False, "configured": oauth_is_configured(), "user": None}
    return {
        "connected": True,
        "configured": oauth_is_configured(),
        "user": {
            "sub": user.get("sub"),
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
        },
    }


def logout_session(session_id: str | None) -> None:
    if not session_id:
        return
    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        (data.get("sessions") or {}).pop(session_id, None)
        _save_store(data)


def disconnect_session(session_id: str | None) -> None:
    if not session_id:
        return
    revoke_token = None
    with _LOCK:
        data = _load_store()
        _cleanup_expired(data)
        sessions = data.get("sessions") or {}
        users = data.get("users") or {}
        row = sessions.pop(session_id, None)
        if row:
            sub = row.get("sub")
            if sub in users:
                tokens = (users.get(sub) or {}).get("tokens") or {}
                revoke_token = (tokens.get("refresh_token") or tokens.get("access_token") or "").strip() or None
                users.pop(sub, None)
        _save_store(data)

    if revoke_token:
        try:
            httpx.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": revoke_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )
        except Exception:
            # Logout should still succeed even if revoke endpoint fails.
            pass


def callback_success_redirect(next_path: str) -> str:
    base = _frontend_base_url()
    safe_next = _safe_next_path(next_path)
    sep = "&" if "?" in safe_next else "?"
    return f"{base}{safe_next}{sep}google_connected=1"


_OAUTH_ERROR_CODES = {
    "missing_code": "missing_code",
    "invalid or expired oauth state": "invalid_state",
    "oauth verifier missing": "invalid_state",
    "token": "token_exchange_failed",
}


def callback_error_redirect(message: str) -> str:
    base = _frontend_base_url()
    raw = (message or "oauth_failed").strip().lower()
    code = "oauth_failed"
    for needle, mapped in _OAUTH_ERROR_CODES.items():
        if needle in raw:
            code = mapped
            break
    if "not configured" in raw:
        code = "not_configured"
    return f"{base}/?google_auth_error={quote_plus(code)}"


def cookie_secure() -> bool:
    return _cookie_secure()

