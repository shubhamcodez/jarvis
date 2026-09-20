"""Inline thumbs on assistant replies — stored signal + prompt hint."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from config import data_root

_LOCK = threading.Lock()
_CACHE: list[dict[str, Any]] | None = None
_CACHE_MAX = 1500


def _path() -> Path:
    p = data_root() / "jarvis-observability" / "reactions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _norm_vote(vote: str) -> str:
    v = (vote or "").strip().lower()
    if v in ("up", "yes", "+", "1", "good"):
        return "up"
    if v in ("down", "no", "-", "0", "bad"):
        return "down"
    return ""


def add_reaction(chat_id: str, vote: str, excerpt: str = "", message_id: str = "") -> dict[str, Any]:
    v = _norm_vote(vote)
    if not v:
        return {"ok": False, "error": "vote must be up or down"}
    rec = {
        "ts": time.time(),
        "chat_id": chat_id or "",
        "message_id": (message_id or "")[:80],
        "vote": v,
        "excerpt": (excerpt or "")[:400],
    }
    with _LOCK:
        with _path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if _CACHE is not None:
            _CACHE.append(rec)
            if len(_CACHE) > _CACHE_MAX:
                del _CACHE[: len(_CACHE) - _CACHE_MAX]
    if v == "down":
        try:
            from memory.facts import add_fact

            note = (excerpt or "").strip().replace("\n", " ")
            text = "User marked a recent assistant reply as unhelpful."
            if note:
                text += f" Excerpt: {note[:180]}"
            text += " Prefer a different approach next time."
            add_fact(text, key="reaction_down", source="reaction")
        except Exception:
            pass
    return {"ok": True, "vote": v, "excerpt": rec["excerpt"]}


def _iter_records() -> list[dict[str, Any]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = _path()
    if not path.is_file():
        _CACHE = []
        return _CACHE
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        _CACHE = []
        return _CACHE
    if len(out) > _CACHE_MAX:
        out = out[-_CACHE_MAX:]
    _CACHE = out
    return _CACHE


def votes_for_chat(chat_id: str) -> dict[str, str]:
    """Latest vote keyed by excerpt (first 240 chars) for this chat."""
    cid = (chat_id or "").strip()
    latest: dict[str, str] = {}
    for rec in _iter_records():
        if cid and (rec.get("chat_id") or "") != cid:
            continue
        key = (rec.get("excerpt") or "")[:240]
        vote = _norm_vote(str(rec.get("vote") or ""))
        if key and vote:
            latest[key] = vote
    return latest


def format_recent_for_prompt(limit: int = 8) -> str:
    recs = _iter_records()[-limit:]
    if not recs:
        return ""
    lines = ["USER REPLY RATINGS (thumbs):"]
    for rec in recs:
        vote = _norm_vote(str(rec.get("vote") or ""))
        mark = "helpful" if vote == "up" else "unhelpful" if vote == "down" else vote
        note = (rec.get("excerpt") or "").replace("\n", " ").strip()[:160]
        if note:
            lines.append(f"- {mark}: {note}")
        else:
            lines.append(f"- {mark}")
    return "\n".join(lines)
