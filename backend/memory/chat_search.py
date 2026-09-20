"""Search chat titles and message bodies (Codex/Cursor “find that conversation”)."""
from __future__ import annotations

from typing import Any

from memory.chat_log import list_chats, read_chat_log


def search_chats(query: str, limit: int = 30) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    hits: list[dict[str, Any]] = []
    for chat in list_chats()[:2000]:
        title = chat.get("title") or ""
        cid = chat.get("id") or ""
        msgs = read_chat_log(cid)[-80:]
        snippet = ""
        score = 0
        title_l = title.lower()
        if q in title_l:
            score += 5
            snippet = title
        for m in msgs:
            if (m.get("role") or "") == "tool":
                continue
            body = (m.get("content") or "")
            if len(body) > 4000:
                body = body[:4000]
            body_l = body.lower()
            if q in body_l:
                score += 1
                if not snippet:
                    idx = body_l.find(q)
                    start = max(0, idx - 40)
                    snippet = body[start : start + 160].replace("\n", " ")
                if score >= 12:
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
    hits.sort(key=lambda x: (-x["score"], x["id"]))
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
        condensed = 0
        for m in older:
            role = m.get("role") or "user"
            text = (m.get("content") or "").replace("\n", " ").strip()
            if not text or role == "tool":
                continue
            lines.append(f"- **{role}:** {text[:180]}")
            condensed += 1
            if condensed >= 40:
                lines.append(f"- … {len(older) - condensed} older messages omitted")
                break
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


def recap_markdown(chat_id: str, extra: str = "") -> str:
    """Codex/Claude-style /recap: structured recovery, not a transcript dump."""
    msgs = read_chat_log(chat_id)
    users = [m for m in msgs if (m.get("role") == "user" and (m.get("content") or "").strip())]
    bots = [m for m in msgs if (m.get("role") == "assistant" and (m.get("content") or "").strip())]
    last_user = (users[-1].get("content") or "").strip() if users else "(none)"
    last_bot = (bots[-1].get("content") or "").strip() if bots else "(none)"
    files = []
    for m in msgs[-40:]:
        text = m.get("content") or ""
        for line in text.splitlines():
            if "```jarvis-file:" in line or "```ada-file:" in line:
                files.append(line.split("-file:", 1)[-1].strip())
    files = list(dict.fromkeys(files))[:12]
    lines = [
        "# Recap",
        "",
        f"**chat_id:** `{chat_id}`",
        f"**turns:** {len(msgs)}  (user {len(users)} / assistant {len(bots)})",
        "",
        "## Current task",
        last_user[:800] or "(none)",
        "",
        "## Last assistant result",
        last_bot[:1200] or "(none)",
        "",
        "## Files touched in this thread",
        ("- " + "\n- ".join(files)) if files else "- (none recorded)",
        "",
        "## Next step",
        "Resume the current task, or say `/btw …` for a side question that stays out of the main plan.",
        "",
    ]
    if extra.strip():
        lines.extend(["## Agent state", extra.strip(), ""])
    return "\n".join(lines).strip()


def handoff_markdown(chat_id: str, extra: str = "") -> str:
    msgs = read_chat_log(chat_id)
    lines = [
        "# Jarvis session handoff",
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
