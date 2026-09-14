"""Load env (secrets), paths, and ada-config.yaml (provider + app settings)."""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def is_packaged() -> bool:
    if getattr(sys, "frozen", False):
        return True
    return os.environ.get("ADA_PACKAGED", "").strip().lower() in ("1", "true", "yes", "on")


def data_root() -> Path:
    """Writable app data: AppData\\Ada when packaged, otherwise the git repo root."""
    env_dir = (os.environ.get("ADA_DATA_DIR") or "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    if is_packaged():
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        p = Path(appdata) / "Ada"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent.parent


# Repo root (parent of backend/) — in a frozen sidecar this is still next to the exe bundle.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = Path(__file__).resolve().parent
_DATA_ROOT = data_root()
load_dotenv(_DATA_ROOT / ".env")
if _DATA_ROOT != _REPO_ROOT:
    load_dotenv(_REPO_ROOT / ".env", override=False)
else:
    load_dotenv(_REPO_ROOT / ".env")

def _config_yaml_path() -> Path:
    packaged = data_root() / "ada-config.yaml"
    bundled = _BACKEND_ROOT / "ada-config.yaml"
    if is_packaged():
        if not packaged.exists() and bundled.exists():
            try:
                packaged.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                return bundled
        return packaged
    return bundled


CONFIG_YAML = _config_yaml_path()
_LEGACY_CONFIG_YAML = _BACKEND_ROOT / "jarvis-config.yaml"

# Legacy paths at repo root (used only if no yaml)
_LLM_PROVIDER_FILE = _REPO_ROOT / "ada-llm-provider.txt"
_LEGACY_LLM_PROVIDER_FILE = _REPO_ROOT / "jarvis-llm-provider.txt"
_GREP_ROOT_FILE = _REPO_ROOT / "ada-grep-root.txt"
_LEGACY_GREP_ROOT_FILE = _REPO_ROOT / "jarvis-grep-root.txt"


_DEFAULTS: dict[str, Any] = {
    "llm_provider": "openai",
    "chat": {
        "history_limit": 300,
        "memory_query_recent_turns": 32,
    },
    "grep": {
        "default_root": None,
    },
    "models": {
        "routing_openai": "gpt-5-mini",
        "routing_xai": "grok-4-1-fast-non-reasoning",
    },
    "autonomy": "gated",
    "desktop_armed": False,
    "workspace_root": None,
}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _yaml_to_load() -> Path | None:
    if CONFIG_YAML.exists():
        return CONFIG_YAML
    if _LEGACY_CONFIG_YAML.exists():
        return _LEGACY_CONFIG_YAML
    return None


def _load_raw_user_config() -> dict[str, Any]:
    """YAML file if present; otherwise legacy .txt files at repo root."""
    yml = _yaml_to_load()
    if yml is not None:
        with yml.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    legacy: dict[str, Any] = {}
    llm_txt = _LLM_PROVIDER_FILE if _LLM_PROVIDER_FILE.exists() else _LEGACY_LLM_PROVIDER_FILE
    if llm_txt.exists():
        p = llm_txt.read_text(encoding="utf-8").strip().lower()
        if p in ("openai", "xai"):
            legacy["llm_provider"] = p
    grep_txt = _GREP_ROOT_FILE if _GREP_ROOT_FILE.exists() else _LEGACY_GREP_ROOT_FILE
    if grep_txt.exists():
        s = grep_txt.read_text(encoding="utf-8").strip()
        if s:
            legacy.setdefault("grep", {})["default_root"] = s
    return legacy


def _merged_config() -> dict[str, Any]:
    return _deep_merge(copy.deepcopy(_DEFAULTS), _load_raw_user_config())


def get_llm_provider() -> str:
    """Current LLM provider: 'openai' or 'xai'. From ada-config.yaml (or legacy files if missing)."""
    prov = str(_merged_config().get("llm_provider") or "openai").strip().lower()
    return prov if prov in ("openai", "xai") else "openai"


def set_llm_provider(provider: str) -> None:
    """Set LLM provider to 'openai' or 'xai'; writes ada-config.yaml."""
    p = (provider or "").strip().lower()
    if p not in ("openai", "xai"):
        raise ValueError("provider must be 'openai' or 'xai'")
    CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    yml = _yaml_to_load()
    if yml is not None:
        with yml.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw["llm_provider"] = p
    _write_merged_yaml(raw)


def get_openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip().strip('"')
    if not key:
        raise ValueError("OPENAI_API_KEY not set. Add it in Settings or in a .env file.")
    return key


def get_xai_api_key() -> str:
    key = (
        os.environ.get("xAI_API_KEY") or os.environ.get("XAI_API_KEY") or ""
    ).strip().strip('"')
    if not key:
        raise ValueError("xAI_API_KEY not set. Add it in Settings or in a .env file.")
    return key


def get_llm_api_key() -> str:
    """API key for the current LLM provider."""
    if get_llm_provider() == "xai":
        return get_xai_api_key()
    return get_openai_api_key()


def _write_merged_yaml(raw: dict[str, Any]) -> None:
    CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(copy.deepcopy(_DEFAULTS), raw)
    with CONFIG_YAML.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            merged,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )


def chats_config_path() -> Path:
    """Path to file storing custom chats directory."""
    return data_root() / "ada-chats-dir.txt"


def chats_dir() -> Path:
    """Directory where chat logs are stored."""
    for p in (chats_config_path(), data_root() / "jarvis-chats-dir.txt", _REPO_ROOT / "ada-chats-dir.txt"):
        if p.exists():
            s = p.read_text(encoding="utf-8").strip()
            if s:
                d = Path(s)
                if d.is_dir() or not d.exists():
                    return d
    return data_root() / "chats"


def get_grep_root() -> Path | None:
    """
    Optional default search root for file grep: ada-config.yaml grep.default_root,
    or ada-grep-root.txt / legacy jarvis-grep-root.txt if yaml is missing.
    """
    raw = (_merged_config().get("grep") or {}).get("default_root")
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    p = Path(s).expanduser().resolve()
    return p if p.is_dir() else None


def get_chat_history_limit() -> int:
    """Max chat log messages sent to the LLM each turn (clamped 1–2000)."""
    v = (_merged_config().get("chat") or {}).get("history_limit", 300)
    try:
        n = int(v)
        return max(1, min(n, 2000))
    except (TypeError, ValueError):
        return 300


def get_memory_query_recent_turns() -> int:
    """Recent messages folded into vector-memory retrieval query (clamped 1–120)."""
    v = (_merged_config().get("chat") or {}).get("memory_query_recent_turns", 32)
    try:
        n = int(v)
        return max(1, min(n, 120))
    except (TypeError, ValueError):
        return 32


def get_routing_model(provider: str) -> str:
    """Small/fast model for supervisor routing (falls back to env / defaults)."""
    models = _merged_config().get("models") or {}
    p = (provider or "").strip().lower()
    if p == "xai":
        env = (os.environ.get("XAI_ROUTING_MODEL") or "").strip()
        return env or str(models.get("routing_xai") or "grok-4-1-fast-non-reasoning")
    env = (os.environ.get("OPENAI_ROUTING_MODEL") or "").strip()
    return env or str(models.get("routing_openai") or "gpt-5-mini")


_AUTONOMY_LEVELS = ("recommend", "draft", "low_risk_auto", "gated", "limited_auto")


def get_autonomy_level() -> str:
    v = str(_merged_config().get("autonomy") or "gated").strip().lower()
    return v if v in _AUTONOMY_LEVELS else "gated"


def set_autonomy_level(level: str) -> None:
    v = (level or "").strip().lower()
    if v not in _AUTONOMY_LEVELS:
        raise ValueError(f"autonomy must be one of {_AUTONOMY_LEVELS}")
    raw: dict[str, Any] = {}
    yml = _yaml_to_load()
    if yml is not None:
        with yml.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw["autonomy"] = v
    _write_merged_yaml(raw)


def is_desktop_armed() -> bool:
    if os.environ.get("ADA_DESKTOP_ARMED", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(_merged_config().get("desktop_armed"))


def set_desktop_armed(armed: bool) -> None:
    raw: dict[str, Any] = {}
    yml = _yaml_to_load()
    if yml is not None:
        with yml.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw["desktop_armed"] = bool(armed)
    _write_merged_yaml(raw)
    os.environ["ADA_DESKTOP_ARMED"] = "1" if armed else "0"


def get_workspace_root() -> str:
    raw = (_merged_config().get("workspace_root") or "").strip()
    if raw:
        return raw
    marker = data_root() / "ada-workspace-root.txt"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    return ""


def set_workspace_root(path: str) -> None:
    p = (path or "").strip()
    raw: dict[str, Any] = {}
    yml = _yaml_to_load()
    if yml is not None:
        with yml.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw["workspace_root"] = p or None
    _write_merged_yaml(raw)
    marker = data_root() / "ada-workspace-root.txt"
    if p:
        marker.write_text(p, encoding="utf-8")
    elif marker.exists():
        marker.unlink()


def api_keys_status() -> dict[str, bool]:
    return {
        "openai_set": bool((os.environ.get("OPENAI_API_KEY") or "").strip()),
        "xai_set": bool(
            (os.environ.get("xAI_API_KEY") or os.environ.get("XAI_API_KEY") or "").strip()
        ),
    }


def write_api_keys(*, openai_key: str | None = None, xai_key: str | None = None) -> None:
    """Persist keys to the data-root .env (never log them). Reload process env."""
    env_path = data_root() / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()
    if openai_key is not None:
        existing["OPENAI_API_KEY"] = openai_key.strip()
        os.environ["OPENAI_API_KEY"] = openai_key.strip()
    if xai_key is not None:
        existing["XAI_API_KEY"] = xai_key.strip()
        existing["xAI_API_KEY"] = xai_key.strip()
        os.environ["XAI_API_KEY"] = xai_key.strip()
        os.environ["xAI_API_KEY"] = xai_key.strip()
    lines = [f"{k}={v}" for k, v in existing.items() if v]
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
