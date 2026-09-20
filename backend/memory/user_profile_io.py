"""Read/write `user_profile.json` (long-lived user facts for prompts / memory)."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from config import data_root

_LEGACY_PROFILE_PATH = Path(__file__).resolve().parent / "user_profile.json"
_LOCK = threading.Lock()


def _profile_path() -> Path:
    dest = data_root() / "memory" / "user_profile.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() and _LEGACY_PROFILE_PATH.is_file():
        try:
            dest.write_bytes(_LEGACY_PROFILE_PATH.read_bytes())
        except OSError:
            return _LEGACY_PROFILE_PATH
    return dest

_DEFAULT: dict = {
    "identity": {"name": None, "pronouns": None, "languages": None},
    "demographics": {"age_range": None, "gender": None, "timezone": None},
    "personality": {
        "communication": None,
        "learning_style": None,
        "risk_tolerance": None,
    },
    "preferences": {
        "tools_stack": None,
        "editor_environment": None,
        "code_style": None,
        "docs_comments": None,
    },
    "goals": {"current_projects": None, "standing_goals": None},
    "boundaries": {"topics_avoid": None, "accessibility_needs": None},
    "appendix_notes": [],
}


def deep_merge_defaults(data: dict | None) -> dict:
    """Return a full profile dict; missing keys filled from defaults."""
    out = copy.deepcopy(_DEFAULT)
    if not data or not isinstance(data, dict):
        return out
    for k, v in data.items():
        if k not in out:
            continue
        if k == "appendix_notes" and isinstance(v, list):
            out[k] = [str(x) for x in v if x is not None and str(x).strip()]
            continue
        if isinstance(v, dict) and isinstance(out[k], dict):
            for sk, sv in v.items():
                if sk in out[k]:
                    out[k][sk] = sv
    return out


def read_user_profile() -> dict:
    path = _profile_path()
    if not path.is_file():
        return deep_merge_defaults(None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deep_merge_defaults(None)
    if not isinstance(raw, dict):
        return deep_merge_defaults(None)
    return deep_merge_defaults(raw)


def write_user_profile(data: dict) -> None:
    merged = deep_merge_defaults(data)
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.json")
    with _LOCK:
        tmp.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
