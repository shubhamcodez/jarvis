"""Persist specialist steps (desktop clicks, coding, shell) to disk + the open span."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import ensure_dirs, obs_dir
from .redact import redact_text
from .spans import current_span, current_trace_id

_LOCK = threading.Lock()
ACTION_FILE = "actions.jsonl"
_MAX_BYTES = 8_000_000


def _path() -> Path:
    ensure_dirs()
    return obs_dir() / "traces" / ACTION_FILE


def log_agent_action(
    *,
    step: Any = None,
    action: str = "",
    description: str = "",
    thought: str = "",
    result: Any = None,
    done: bool = False,
    agent: Optional[str] = None,
) -> None:
    rec = {
        "ts": time.time(),
        "step": step,
        "action": redact_text(str(action or ""), max_len=120),
        "description": redact_text(str(description or ""), max_len=400),
        "thought": redact_text(str(thought or ""), max_len=300),
        "result": redact_text("" if result is None else str(result), max_len=400),
        "done": bool(done),
    }
    if agent:
        rec["agent"] = str(agent)[:40]
    tid = current_trace_id()
    if tid:
        rec["trace_id"] = tid
    try:
        path = _path()
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            if path.stat().st_size > _MAX_BYTES:
                _rotate(path)
    except OSError:
        pass
    sp = current_span()
    if sp is not None:
        try:
            sp.event(
                "agent_step",
                step=step,
                action=rec["action"],
                description=rec["description"][:200],
                done=bool(done),
            )
        except Exception:
            pass


def _rotate(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = lines[-(len(lines) // 2) :] if len(lines) > 2000 else lines
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def list_actions(limit: int = 200, trace_id: Optional[str] = None) -> list[dict[str, Any]]:
    from .config import read_jsonl_records

    rows = read_jsonl_records("traces", ACTION_FILE, limit=max(limit, 200))
    if trace_id:
        rows = [r for r in rows if r.get("trace_id") == trace_id]
    return rows[-max(1, limit) :]
