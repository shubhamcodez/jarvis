"""In-session /loop (Claude Code / Cursor). Ticks while the backend is up."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import chats_dir, data_root, in_quiet_hours

_LOCK = threading.Lock()
_INTERVAL_RE = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)?$", re.IGNORECASE)
_DEFAULT_PROMPT = "Brief maintenance check: anything blocked, waiting, or failed? Two sentences."


def _path() -> Path:
    d = data_root() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d / "session_loops.json"


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        legacy = Path(chats_dir()) / "session_loops.json"
        if legacy.exists():
            path = legacy
        else:
            return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("loops"), list):
            return data["loops"]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save(items: list[dict[str, Any]]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_interval(token: str) -> Optional[int]:
    """Return seconds. 30s, 5m, 1h, or a bare number as minutes. None if not an interval."""
    raw = (token or "").strip().lower()
    if not raw:
        return None
    m = _INTERVAL_RE.fullmatch(raw)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "m").lower()
    if unit == "s":
        sec = int(n)
    elif unit == "h":
        sec = int(n * 3600)
    else:
        sec = int(n * 60)
    return max(30, min(sec, 6 * 3600))


def list_loops(chat_id: str = "") -> list[dict[str, Any]]:
    items = _load()
    if chat_id:
        items = [x for x in items if x.get("chat_id") == chat_id]
    return items


def start_loop(chat_id: str, interval_sec: int, prompt: str = "") -> dict[str, Any]:
    if not chat_id:
        return {"ok": False, "error": "no active chat"}
    now = time.time()
    rec = {
        "id": "lp_" + uuid.uuid4().hex[:10],
        "chat_id": chat_id,
        "interval_sec": max(30, min(int(interval_sec), 6 * 3600)),
        "prompt": (prompt or "").strip() or _DEFAULT_PROMPT,
        "created_at": now,
        "next_run_at": now + max(30, min(int(interval_sec), 6 * 3600)),
        "fires": 0,
        "last_error": "",
    }
    with _LOCK:
        items = [x for x in _load() if not (x.get("chat_id") == chat_id and x.get("prompt") == rec["prompt"])]
        items.append(rec)
        _save(items[-20:])
    return {"ok": True, **rec}


def stop_loops(chat_id: str, loop_id: str = "") -> int:
    with _LOCK:
        items = _load()
        before = len(items)
        if loop_id:
            items = [x for x in items if x.get("id") != loop_id]
        elif chat_id:
            items = [x for x in items if x.get("chat_id") != chat_id]
        _save(items)
        return before - len(items)


def _fire(item: dict[str, Any]) -> None:
    from agents.models import get_llm_client
    from config import get_llm_api_key, get_llm_provider
    from memory.chat_log import append_chat_log

    prompt = item.get("prompt") or _DEFAULT_PROMPT
    chat_id = item.get("chat_id") or ""
    label = f"/loop {item.get('interval_sec')}s"
    user_line = f"{label} {prompt}"
    try:
        append_chat_log("user", user_line, chat_id)
        if in_quiet_hours():
            append_chat_log("assistant", "**Loop** skipped (quiet hours).", chat_id)
            return
        client = get_llm_client(get_llm_provider())
        reply = client.chat(
            get_llm_api_key(),
            f"Loop check-in (short status only; do not start an agent plan):\n{prompt}",
            None,
            None,
            "You are Jarvis doing a scheduled check-in. Reply in at most 6 lines.",
        )
        extra = ""
        low = (prompt or "").lower()
        if any(w in low for w in ("test", "pytest", "npm test", "cargo test", "go test", "run_tests")):
            try:
                from config import get_workspace_root
                from tools.diagnostics import run_python_tests
                from tools.overlay_workspace import OverlayWorkspace

                root = get_workspace_root()
                if root:
                    tests = run_python_tests(OverlayWorkspace(root), include_all=True)
                    extra = (
                        "\n\nWorkspace tests (read-only):\n"
                        + str(tests.get("summary") or tests.get("error") or "")[:1200]
                    )
            except Exception as te:
                extra = f"\n\nWorkspace tests skipped: {te}"
        try:
            from observability.redact import redact_text

            reply = redact_text(reply or "", max_len=2000)
            extra = redact_text(extra, max_len=1400)
        except Exception:
            pass
        append_chat_log("assistant", f"**Loop**\n\n{reply or '(no reply)'}{extra}", chat_id)
        item["last_error"] = ""
    except Exception as e:
        item["last_error"] = str(e)[:240]
        try:
            append_chat_log("assistant", f"**Loop failed:** {item['last_error']}", chat_id)
        except Exception:
            pass


def run_due_loops_sync() -> int:
    now = time.time()
    n = 0
    with _LOCK:
        items = _load()
        due = [x for x in items if float(x.get("next_run_at") or 0) <= now]
    for item in due[:4]:
        _fire(item)
        item["fires"] = int(item.get("fires") or 0) + 1
        item["next_run_at"] = time.time() + int(item.get("interval_sec") or 300)
        n += 1
    if due:
        with _LOCK:
            by_id = {x.get("id"): x for x in _load()}
            for item in due:
                by_id[item.get("id")] = item
            _save(list(by_id.values()))
    return n


def loops_markdown(chat_id: str = "") -> str:
    items = list_loops(chat_id)
    if not items:
        return "No in-session loops. Start one with `/loop 5m check the deploy`."
    lines = ["# Loops", ""]
    for x in items:
        nxt = int(max(0, float(x.get("next_run_at") or 0) - time.time()))
        lines.append(
            f"- `{x.get('id')}` every {x.get('interval_sec')}s — {x.get('prompt')[:120]} "
            f"(next in {nxt}s, fires={x.get('fires') or 0})"
        )
    lines.append("")
    lines.append("`/loop stop` cancels loops for this chat.")
    return "\n".join(lines)
