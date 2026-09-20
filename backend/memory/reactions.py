"""Inline thumbs on assistant replies — Claude Code / Cursor request."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config import data_root


def _path() -> Path:
    p = data_root() / "ada-observability" / "reactions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def add_reaction(chat_id: str, vote: str, excerpt: str = "") -> dict[str, Any]:
    v = (vote or "").strip().lower()
    if v in ("up", "yes", "+", "1", "good"):
        v = "up"
    elif v in ("down", "no", "-", "0", "bad"):
        v = "down"
    else:
        return {"ok": False, "error": "vote must be up or down"}
    rec = {
        "ts": time.time(),
        "chat_id": chat_id or "",
        "vote": v,
        "excerpt": (excerpt or "")[:400],
    }
    with _path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if v == "down":
        try:
            from memory.facts import add_fact

            add_fact(
                "User marked a recent assistant reply as unhelpful. Prefer a different approach next time.",
                source="reaction",
            )
        except Exception:
            pass
    return {"ok": True, "vote": v}
