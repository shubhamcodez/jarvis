"""Encrypted-at-rest LLM API keys (same box as OAuth). Env vars still win."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

_LOCK = Lock()


def _path() -> Path:
    try:
        from config import data_root

        return data_root() / ".secrets" / "llm-keys.json"
    except Exception:
        return Path(__file__).resolve().parents[2] / ".secrets" / "llm-keys.json"


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    if "blob" in raw:
        from auth.secret_box import open_json

        opened = open_json(raw)
        return opened if isinstance(opened, dict) else {}
    return raw


def _save(data: dict[str, Any]) -> None:
    from auth.secret_box import seal_json

    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = seal_json(data)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get_key(name: str) -> str:
    with _LOCK:
        data = _load()
    return str((data or {}).get(name) or "").strip()


def set_keys(*, openai: str | None = None, xai: str | None = None) -> None:
    with _LOCK:
        data = _load()
        if openai is not None:
            if openai.strip():
                data["OPENAI_API_KEY"] = openai.strip()
            else:
                data.pop("OPENAI_API_KEY", None)
        if xai is not None:
            if xai.strip():
                data["XAI_API_KEY"] = xai.strip()
            else:
                data.pop("XAI_API_KEY", None)
                data.pop("xAI_API_KEY", None)
        _save(data)
