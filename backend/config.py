"""Load env (secrets), paths, and jarvis-config.yaml (provider + app settings)."""
from __future__ import annotations

import copy
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def is_packaged() -> bool:
    if getattr(sys, "frozen", False):
        return True
    return os.environ.get("ADA_PACKAGED", "").strip().lower() in ("1", "true", "yes", "on")


def data_root() -> Path:
    """Writable app data: AppData\\Jarvis when packaged, otherwise the git repo root."""
    env_dir = (os.environ.get("ADA_DATA_DIR") or "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    if is_packaged():
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            p = Path(appdata) / "Jarvis"
        elif sys.platform == "darwin":
            p = Path.home() / "Library" / "Application Support" / "Jarvis"
        else:
            xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
            p = Path(xdg) / "Jarvis" if xdg else Path.home() / ".local" / "share" / "Jarvis"
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
    packaged = data_root() / "jarvis-config.yaml"
    bundled = _BACKEND_ROOT / "jarvis-config.yaml"
    if is_packaged():
        return packaged
    return bundled


CONFIG_YAML = _config_yaml_path()
_LEGACY_CONFIG_YAML = _BACKEND_ROOT / "jarvis-config.yaml"

# Legacy paths at repo root (used only if no yaml)
_LLM_PROVIDER_FILE = _REPO_ROOT / "ada-llm-provider.txt"
_LEGACY_LLM_PROVIDER_FILE = _REPO_ROOT / "jarvis-llm-provider.txt"
_GREP_ROOT_FILE = _REPO_ROOT / "jarvis-grep-root.txt"
_LEGACY_GREP_ROOT_FILE = _REPO_ROOT / "jarvis-grep-root.txt"


_DEFAULTS: dict[str, Any] = {
    "llm_provider": "openai",
    "chat": {
        "history_limit": 80,
        "memory_query_recent_turns": 16,
    },
    "context": {
        "system_stable_tokens": 1800,
        "system_dynamic_tokens": 2400,
        "history_tokens": 6000,
        "memory_tokens": 1400,
        "facts_tokens": 500,
        "identity_tokens": 900,
    },
    "grep": {
        "default_root": None,
    },
    "models": {
        "routing_openai": "gpt-5-mini",
        "routing_xai": "grok-4-1-fast-non-reasoning",
        "local_model_id": None,
    },
    "autonomy": "gated",
    "desktop_armed": False,
    "workspace_root": None,
    "run_mode": "agent",
    "spend": {
        "max_tokens_per_run": 80000,
        "warn_tokens": 40000,
    },
    "quiet_hours": {
        "enabled": False,
        "start": "23:00",
        "end": "08:00",
        "timezone": "local",
    },
}


_LOG = logging.getLogger("ada.config")
_WRITE_LOCK = threading.Lock()


def _seed_packaged_config() -> None:
    if not is_packaged() or CONFIG_YAML.exists():
        return
    try:
        CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_YAML.write_text(
            yaml.safe_dump(_DEFAULTS, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError:
        pass


_seed_packaged_config()


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
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
        try:
            with yml.open(encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            _LOG.warning("Failed to parse %s: %s", yml, exc)
            return {}
        return raw if isinstance(raw, dict) else {}

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


_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_CACHE_MTIME = 0.0


def _invalidate_config_cache() -> None:
    global _CONFIG_CACHE, _CONFIG_CACHE_MTIME
    _CONFIG_CACHE = None
    _CONFIG_CACHE_MTIME = 0.0


def _merged_config() -> dict[str, Any]:
    global _CONFIG_CACHE, _CONFIG_CACHE_MTIME
    yml = _yaml_to_load()
    try:
        mtime = float(yml.stat().st_mtime) if yml is not None and yml.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _CONFIG_CACHE is not None and mtime == _CONFIG_CACHE_MTIME:
        return copy.deepcopy(_CONFIG_CACHE)
    merged = _deep_merge(copy.deepcopy(_DEFAULTS), _load_raw_user_config())
    _CONFIG_CACHE = merged
    _CONFIG_CACHE_MTIME = mtime
    return copy.deepcopy(merged)


_PROVIDERS = ("openai", "xai", "local")


def get_llm_provider() -> str:
    """Current LLM provider: 'openai', 'xai', or 'local'."""
    prov = str(_merged_config().get("llm_provider") or "openai").strip().lower()
    if prov not in _PROVIDERS:
        _LOG.warning("Invalid llm_provider %r; falling back to openai", prov)
        return "openai"
    return prov


def set_llm_provider(provider: str) -> None:
    """Set LLM provider; writes jarvis-config.yaml."""
    p = (provider or "").strip().lower()
    if p not in _PROVIDERS:
        raise ValueError("provider must be 'openai', 'xai', or 'local'")
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
    """API key for the current LLM provider. Local models do not need a key."""
    p = get_llm_provider()
    if p == "local":
        return "local"
    if p == "xai":
        return get_xai_api_key()
    return get_openai_api_key()


def get_local_model_id() -> str:
    mid = (_merged_config().get("models") or {}).get("local_model_id")
    return str(mid or "").strip()


def set_local_model_id(model_id: str) -> None:
    raw: dict[str, Any] = {}
    yml = _yaml_to_load()
    if yml is not None:
        with yml.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    models = dict(raw.get("models") or {})
    models["local_model_id"] = (model_id or "").strip() or None
    raw["models"] = models
    _write_merged_yaml(raw)


def _load_raw_for_write() -> dict[str, Any]:
    return dict(_load_raw_user_config())


def _write_merged_yaml(raw: dict[str, Any]) -> None:
    with _WRITE_LOCK:
        CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
        merged = _deep_merge(copy.deepcopy(_DEFAULTS), raw)
        tmp = CONFIG_YAML.with_suffix(".yaml.tmp")
        tmp.write_text(
            yaml.safe_dump(
                merged,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(CONFIG_YAML)
        _invalidate_config_cache()


def chats_config_path() -> Path:
    """Path to file storing custom chats directory."""
    return data_root() / "ada-chats-dir.txt"


def chats_dir() -> Path:
    """Directory where chat logs are stored."""
    for p in (chats_config_path(), data_root() / "jarvis-chats-dir.txt", _REPO_ROOT / "ada-chats-dir.txt"):
        if p.exists():
            s = p.read_text(encoding="utf-8").strip()
            if s:
                d = Path(s).expanduser()
                try:
                    d = d.resolve()
                except OSError:
                    pass
                if d.is_dir() or not d.exists():
                    if not d.exists():
                        try:
                            d.mkdir(parents=True, exist_ok=True)
                        except OSError:
                            pass
                    return d
    return data_root() / "chats"


def get_grep_root() -> Path | None:
    """
    Optional default search root for file grep: jarvis-config.yaml grep.default_root,
    or jarvis-grep-root.txt / legacy jarvis-grep-root.txt if yaml is missing.
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
    v = (_merged_config().get("chat") or {}).get("history_limit", 80)
    try:
        n = int(v)
        return max(1, min(n, 2000))
    except (TypeError, ValueError):
        return 80


def get_context_budgets() -> dict[str, int]:
    """Token budgets for context assembly (stable prefix + dynamic + history)."""
    raw = _merged_config().get("context") or {}
    defaults = _DEFAULTS["context"]
    out: dict[str, int] = {}
    for k, fallback in defaults.items():
        try:
            out[k] = max(64, min(32_000, int(raw.get(k, fallback))))
        except (TypeError, ValueError):
            out[k] = int(fallback)
    return out


def get_memory_query_recent_turns() -> int:
    """Recent messages folded into vector-memory retrieval query (clamped 1–120)."""
    v = (_merged_config().get("chat") or {}).get("memory_query_recent_turns", 16)
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
    if p == "local":
        return str(models.get("local_model_id") or "local")
    env = (os.environ.get("OPENAI_ROUTING_MODEL") or "").strip()
    return env or str(models.get("routing_openai") or "gpt-5-mini")


_AUTONOMY_LEVELS = ("recommend", "draft", "low_risk_auto", "gated", "limited_auto")


def get_autonomy_level() -> str:
    v = str(_merged_config().get("autonomy") or "gated").strip().lower()
    if v not in _AUTONOMY_LEVELS:
        _LOG.warning("Invalid autonomy %r; falling back to gated", v)
        return "gated"
    return v


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
    env = os.environ.get("ADA_DESKTOP_ARMED", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
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


_RUN_MODES = ("plan", "draft", "agent")


def get_run_mode() -> str:
    v = str(_merged_config().get("run_mode") or "agent").strip().lower()
    if v not in _RUN_MODES:
        _LOG.warning("Invalid run_mode %r; falling back to agent", v)
        return "agent"
    return v


def set_run_mode(mode: str) -> str:
    v = (mode or "").strip().lower()
    if v not in _RUN_MODES:
        raise ValueError(f"run_mode must be one of {_RUN_MODES}")
    raw = _load_raw_for_write()
    raw["run_mode"] = v
    _write_merged_yaml(raw)
    return v


def get_spend_limits() -> dict[str, int]:
    spend = _merged_config().get("spend") or {}
    try:
        cap = max(1000, min(2_000_000, int(spend.get("max_tokens_per_run") or 80000)))
    except (TypeError, ValueError):
        cap = 80000
    try:
        warn = max(500, min(cap, int(spend.get("warn_tokens") or cap // 2)))
    except (TypeError, ValueError):
        warn = cap // 2
    return {"max_tokens_per_run": cap, "warn_tokens": warn}


def set_spend_limits(*, max_tokens_per_run: int | None = None, warn_tokens: int | None = None) -> dict[str, int]:
    cur = get_spend_limits()
    if max_tokens_per_run is not None:
        cur["max_tokens_per_run"] = max(1000, min(2_000_000, int(max_tokens_per_run)))
    if warn_tokens is not None:
        cur["warn_tokens"] = max(500, min(cur["max_tokens_per_run"], int(warn_tokens)))
    raw = _load_raw_for_write()
    raw["spend"] = cur
    _write_merged_yaml(raw)
    return cur


def _parse_hhmm(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    try:
        datetime.strptime(raw, "%H:%M")
        return raw
    except ValueError:
        try:
            datetime.strptime(fallback, "%H:%M")
            return fallback
        except ValueError:
            return "00:00"


def get_quiet_hours() -> dict[str, Any]:
    qh = _merged_config().get("quiet_hours") or {}
    start = _parse_hhmm(str(qh.get("start") or "23:00"), "23:00")
    end = _parse_hhmm(str(qh.get("end") or "08:00"), "08:00")
    tz = str(qh.get("timezone") or "local").strip() or "local"
    return {
        "enabled": bool(qh.get("enabled")),
        "start": start,
        "end": end,
        "timezone": tz[:80],
    }


def set_quiet_hours(
    *,
    enabled: bool | None = None,
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    cur = get_quiet_hours()
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if start:
        cur["start"] = _parse_hhmm(str(start).strip(), cur["start"])
    if end:
        cur["end"] = _parse_hhmm(str(end).strip(), cur["end"])
    if timezone:
        cur["timezone"] = str(timezone).strip()[:80] or "local"
    raw = _load_raw_for_write()
    raw["quiet_hours"] = cur
    _write_merged_yaml(raw)
    return cur


def in_quiet_hours(now: datetime | None = None) -> bool:
    """True when quiet hours are enabled and the current local/configured time is inside the window."""
    qh = get_quiet_hours()
    if not qh.get("enabled"):
        return False
    start = _parse_hhmm(str(qh.get("start") or "23:00"), "23:00")
    end = _parse_hhmm(str(qh.get("end") or "08:00"), "08:00")
    current = now or datetime.now()
    tz_name = str(qh.get("timezone") or "local").strip()
    if tz_name and tz_name.lower() != "local":
        try:
            from zoneinfo import ZoneInfo

            current = datetime.now(ZoneInfo(tz_name))
        except Exception:
            current = datetime.now()
    hhmm = current.strftime("%H:%M")
    if start <= end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end


def write_api_keys(*, openai_key: str | None = None, xai_key: str | None = None) -> None:
    """Persist keys to the data-root .env (never log them). Reload process env."""
    env_path = data_root() / ".env"
    existing: dict[str, str] = {}
    comments: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                comments.append(line)
                continue
            if "=" not in line:
                comments.append(line)
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip().strip('"').strip("'")
    if openai_key is not None:
        val = openai_key.strip()
        if "\n" in val or "\r" in val:
            raise ValueError("API key must not contain newlines")
        if val:
            existing["OPENAI_API_KEY"] = val
            os.environ["OPENAI_API_KEY"] = val
        else:
            existing.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
    if xai_key is not None:
        val = xai_key.strip()
        if "\n" in val or "\r" in val:
            raise ValueError("API key must not contain newlines")
        existing.pop("xAI_API_KEY", None)
        if val:
            existing["XAI_API_KEY"] = val
            os.environ["XAI_API_KEY"] = val
            os.environ.pop("xAI_API_KEY", None)
        else:
            existing.pop("XAI_API_KEY", None)
            os.environ.pop("XAI_API_KEY", None)
            os.environ.pop("xAI_API_KEY", None)
    lines = list(comments)
    for k, v in existing.items():
        if not v:
            continue
        safe = v.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{k}="{safe}"')
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
