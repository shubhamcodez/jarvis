"""
Trace logging: every agent/chat run logs provider, route, message, reply, steps, success, error, duration, token estimates.
Success rates and token/error stats can be aggregated per model.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from .config import ensure_dirs, obs_dir

try:
    from tools.sandbox_markdown import redact_markdown_chart_embeds
except ImportError:
    def redact_markdown_chart_embeds(text: str) -> str:
        return text or ""

TRACE_FILE = "trace.jsonl"
_MAX_LINE = 100_000  # cap lines per file, then rotate


def _trace_path() -> Path:
    ensure_dirs()
    return obs_dir() / "traces" / TRACE_FILE


def get_trace_log_path() -> str:
    return str(_trace_path())


def _estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 chars per token for English."""
    return max(0, (len(text or "") + 3) // 4)


def trace_log(
    provider: str,
    route: str,
    message: str,
    reply: str,
    success: bool = True,
    error: Optional[str] = None,
    duration_sec: Optional[float] = None,
    step_count: Optional[int] = None,
    token_input: Optional[int] = None,
    token_output: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """
    Append one trace record. Called after each chat/agent run.
    provider: "openai" | "xai"
    route: "chat" | "run_desktop" | "run_coding" | "run_shell" | "run_finance" | ...
    """
    ensure_dirs()
    path = _trace_path()
    if token_input is None:
        token_input = _estimate_tokens(message)
    if token_output is None:
        token_output = _estimate_tokens(reply)
    extra_safe = dict(extra or {})
    for reserved in (
        "ts",
        "provider",
        "route",
        "message",
        "reply",
        "success",
        "error",
        "duration_sec",
        "step_count",
        "token_input",
        "token_output",
    ):
        extra_safe.pop(reserved, None)
    record = {
        **extra_safe,
        "ts": time.time(),
        "provider": provider,
        "route": route,
        "message": (message or "")[:2000],
        "reply": redact_markdown_chart_embeds(reply or "")[:4000],
        "success": success,
        "error": error,
        "duration_sec": duration_sec,
        "step_count": step_count,
        "token_input": token_input,
        "token_output": token_output,
    }
    try:
        from .spans import current_trace_id

        tid = current_trace_id()
        if tid:
            record["trace_id"] = tid
    except Exception:
        pass
    try:
        from .redact import redact_text, redact_value
        from .metrics import incr, observe

        record["message"] = redact_text(str(record.get("message") or ""), max_len=2000)
        record["reply"] = redact_text(str(record.get("reply") or ""), max_len=4000)
        record = redact_value(record)
        incr("turns.total", provider=provider, route=route)
        incr("turns.success" if success else "turns.error", provider=provider, route=route)
        observe("turn.duration_sec", float(duration_sec or 0), provider=provider)
        observe("turn.tokens_in", float(token_input or 0), provider=provider)
        observe("turn.tokens_out", float(token_output or 0), provider=provider)
    except Exception:
        pass
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
    except Exception:
        pass


def _rotate_if_needed(path: Path) -> None:
    try:
        # Cheap line-count via size heuristic + occasional full rotate.
        if path.stat().st_size < 8_000_000:
            return
        rotated = path.with_suffix(".jsonl.1")
        try:
            rotated.unlink(missing_ok=True)
        except TypeError:
            if rotated.exists():
                rotated.unlink()
        except OSError:
            pass
        path.replace(rotated)
    except OSError:
        pass


def list_traces(limit: int = 500) -> list[dict]:
    """Read latest trace records from Jarvis (plus leftover Ada file if needed)."""
    from .config import read_jsonl_records

    ensure_dirs()
    return read_jsonl_records("traces", TRACE_FILE, limit=max(1, limit))
