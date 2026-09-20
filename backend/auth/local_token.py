"""Per-install API token so loopback is not an open shell/HITL/keys API.

The token lives in data_root/.secrets/jarvis-api-token (0600). Vite injects it on
the dev proxy; Tauri reads the same file. Nothing HTTP-serves the token.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

_TOKEN: Optional[str] = None


def token_path() -> Path:
    raw = (os.environ.get("ADA_API_TOKEN_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    try:
        from config import data_root

        return data_root() / ".secrets" / "jarvis-api-token"
    except Exception:
        return Path(__file__).resolve().parents[2] / ".secrets" / "jarvis-api-token"


def get_or_create_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    env = (os.environ.get("ADA_API_TOKEN") or "").strip()
    if env:
        _TOKEN = env
        return _TOKEN
    path = token_path()
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                _TOKEN = existing
                return _TOKEN
    except OSError:
        pass
    token = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    _TOKEN = token
    return _TOKEN


def verify_token(provided: Optional[str]) -> bool:
    expected = get_or_create_token()
    got = (provided or "").strip()
    if not expected or not got:
        return False
    return hmac.compare_digest(got, expected)


def token_from_headers(headers) -> Optional[str]:
    raw = (headers.get("x-jarvis-token") or headers.get("X-Jarvis-Token") or "").strip()
    if raw:
        return raw
    auth = (headers.get("authorization") or headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def request_has_valid_token(request, *, allow_query: bool = False) -> bool:
    token = token_from_headers(request.headers)
    if verify_token(token):
        return True
    if not allow_query:
        return False
    q = (request.query_params.get("token") or "").strip()
    return verify_token(q)


def ws_has_valid_token(ws) -> bool:
    token = token_from_headers(ws.headers)
    if verify_token(token):
        return True
    q = (ws.query_params.get("token") or "").strip()
    return verify_token(q)
