"""Search chat titles and message bodies (Codex/Cursor “find that conversation”)."""
from __future__ import annotations

from typing import Any

from memory.chat_log import list_chats, read_chat_log


def search_chats(query: str, limit: int = 30) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    hits: list[dict[str, Any]] = []
    for chat in list_chats():
        title = chat.get("title") or ""
        cid = chat.get("id") or ""
        msgs = read_chat_log(cid)
        snippet = ""
        score = 0
        if q in title.lower():
            score += 5
            snippet = title
        for m in msgs:
            body = (m.get("content") or "")
            if q in body.lower():
                score += 1
                if not snippet:
                    idx = body.lower().find(q)
                    start = max(0, idx - 40)
                    snippet = body[start : start + 160].replace("\n", " ")
                break
        if score:
            hits.append(
                {
                    "id": cid,
                    "title": title or "Chat",
                    "snippet": snippet[:200],
                    "score": score,
                }
            )
        if len(hits) >= limit * 3:
            break
    hits.sort(key=lambda x: (-x["score"], x["id"]), reverse=False)
    hits.sort(key=lambda x: -x["score"])
    return hits[:limit]


def extractive_compact(chat_id: str, keep_recent: int = 8) -> str:
    """Visible compact summary without requiring a second model call."""
    msgs = read_chat_log(chat_id)
    if not msgs:
        return "Nothing to compact — this chat is empty."
    older = msgs[:-keep_recent] if len(msgs) > keep_recent else []
    recent = msgs[-keep_recent:]
    lines = ["# Compacted context (visible summary)", ""]
    if older:
        lines.append(f"## Earlier ({len(older)} messages, condensed)")
        for m in older:
            role = m.get("role") or "user"
            text = (m.get("content") or "").replace("\n", " ").strip()
            if not text or role == "tool":
                continue
            lines.append(f"- **{role}:** {text[:180]}")
        lines.append("")
    lines.append(f"## Recent ({len(recent)} messages kept verbatim in the log)")
    for m in recent:
        role = m.get("role") or "user"
        text = (m.get("content") or "").strip()
        if role == "tool":
            continue
        lines.append(f"**{role}:** {text[:400]}")
        lines.append("")
    return "\n".join(lines).strip()


def handoff_markdown(chat_id: str, extra: str = "") -> str:
    msgs = read_chat_log(chat_id)
    lines = [
        "# Ada session handoff",
        "",
        "Paste this into a new chat (or another tool) so you do not re-explain the thread.",
        "",
        f"**chat_id:** `{chat_id}`",
        "",
        "## Decisions and last exchanges",
        "",
    ]
    for m in msgs[-24:]:
        role = m.get("role") or "user"
        if role == "tool":
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        lines.append(f"### {role}")
        lines.append(text[:2500])
        lines.append("")
    if extra.strip():
        lines.append("## Extra")
        lines.append(extra.strip())
    return "\n".join(lines).strip()
