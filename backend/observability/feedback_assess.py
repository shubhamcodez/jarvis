"""
User-triggered quality review: when the user says the last reply was unsatisfactory,
load chat history (excluding the complaint), optionally re-run the triggering user
message on the *other* provider, and ask an LLM (prefer OpenAI) for diagnosis + fixes.

This replaces the old idea of auto-generating eval cases from traces on every turn;
those eval cases are now opt-in via env (see auto_loop.py).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from config import get_llm_provider, get_openai_api_key, get_xai_api_key
from agents.models import get_llm_client
from memory.chat_log import read_chat_log

# Max turns sent to the assessor (user+assistant pairs, tool rows excluded from "pair" logic but listed lightly)
_MAX_TRANSCRIPT_TURNS = 50


def is_feedback_complaint(text: str) -> bool:
    """True if the message is likely 'this response was bad' (short, phrase-based)."""
    t = (text or "").strip().lower()
    if not t or len(t) > 320:
        return False
    phrases = (
        "don't like this response",
        "do not like this response",
        "don't like that response",
        "don't like this answer",
        "don't like this reply",
        "don't like that answer",
        "don't like that reply",
        "this response isn't",
        "this response is not",
        "this answer isn't",
        "this answer is not",
        "this isn't right",
        "this is not right",
        "this was wrong",
        "doesn't look right",
        "does not look right",
        "bad response",
        "bad answer",
        "bad reply",
        "wasn't helpful",
        "was not helpful",
        "this response was not helpful",
        "this reply was not helpful",
        "wrong answer",
        "something's wrong with this response",
        "something is wrong with this response",
        "something is wrong with this reply",
        "not a good response",
        "poor response",
        "terrible response",
        "that didn't help",
        "that did not help",
        "i don't like this",
        "i do not like this",
    )
    if any(p in t for p in phrases):
        return True
    if len(t) < 120:
        if "don't like this" in t or "do not like this" in t:
            return True
        if "not right" in t and ("response" in t or "answer" in t or "reply" in t):
            return True
        if "looks wrong" in t or "seems wrong" in t:
            return True
    return False


def _dialogue_only(messages: list[dict]) -> list[dict]:
    return [m for m in messages if (m.get("role") or "") in ("user", "assistant")]


def _last_user_assistant_pair(dialogue: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """Last assistant in dialogue and the user message immediately before it."""
    for i in range(len(dialogue) - 1, -1, -1):
        if dialogue[i].get("role") != "assistant":
            continue
        assistant_content = (dialogue[i].get("content") or "").strip()
        j = i - 1
        while j >= 0 and dialogue[j].get("role") != "user":
            j -= 1
        if j < 0:
            return None, assistant_content
        user_content = (dialogue[j].get("content") or "").strip()
        return user_content, assistant_content
    return None, None


def _pick_assessor_key_and_provider() -> tuple[str, str]:
    """Prefer OpenAI for meta-assessment; fall back to xAI if needed."""
    try:
        return get_openai_api_key(), "openai"
    except ValueError:
        pass
    try:
        return get_xai_api_key(), "xai"
    except ValueError as exc:
        raise ValueError("No assessor API key configured") from exc


def _other_provider(p: str) -> str:
    return "xai" if (p or "").strip().lower() == "openai" else "openai"


def _try_single_turn_reply(provider: str, user_message: str) -> Optional[str]:
    if not user_message.strip():
        return None
    try:
        if provider == "openai":
            key = get_openai_api_key()
        else:
            key = get_xai_api_key()
    except ValueError:
        return None
    client = get_llm_client(provider)
    try:
        return client.chat(
            key,
            user_message,
            None,
            None,
            "You are a helpful, accurate assistant. Answer clearly and concisely.",
        )
    except Exception as e:
        return f"[Could not run {provider}: {e}]"


ASSESSOR_SYSTEM = """You are a senior reviewer for an AI assistant product (Ada).

The user said they are unhappy with the most recent assistant reply before their complaint.

You receive:
- A transcript of user/assistant turns (and note if tool outputs appeared).
- The specific user message that preceded the unsatisfactory assistant reply, and that assistant reply.
- Optionally, how a *different* LLM provider answered the *same* user message in isolation (no prior chat context) for comparison.

Your job:
1. Diagnose what likely went wrong (reasoning, facts, tone, completeness, tool/code use, calculations, formatting, etc.).
2. Give concrete, actionable suggestions — not limited to prompt edits (e.g. change a calculation approach, add validation, adjust routing, improve tool choice, UX copy).
3. If an alternate model answer is shown and is clearly stronger, say what we can learn.

Respond in clear **Markdown** with short sections and bullet points where useful. Be direct."""


def run_feedback_assessment(chat_id: str, selected_provider: Optional[str] = None) -> dict[str, Any]:
    """
    Load chat log for chat_id (last message must be user complaint). Returns a dict:
    ok, assessment (markdown), alternate_provider, alternate_reply, error, meta.
    """
    selected_provider = (selected_provider or get_llm_provider()).strip().lower()
    if selected_provider not in ("openai", "xai"):
        selected_provider = "openai"

    msgs = read_chat_log(chat_id)
    if not msgs:
        return {"ok": False, "error": "No chat history found for this chat."}
    last = msgs[-1]
    if (last.get("role") or "") != "user":
        return {"ok": False, "error": "Last message is not a user message; cannot assess feedback."}
    if not is_feedback_complaint(last.get("content") or ""):
        return {"ok": False, "error": "Message does not look like a quality complaint."}

    prefix = msgs[:-1]
    dialogue = _dialogue_only(prefix)
    if not dialogue:
        return {"ok": False, "error": "No assistant reply before your message to review."}

    trigger_user, bad_assistant = _last_user_assistant_pair(dialogue)
    if not bad_assistant:
        return {"ok": False, "error": "Could not find a prior assistant message to review."}

    # Transcript slice (tail)
    tail = dialogue[-_MAX_TRANSCRIPT_TURNS:]
    transcript_lines = []
    for m in tail:
        r = m.get("role") or "?"
        c = (m.get("content") or "").strip()
        if len(c) > 8000:
            c = c[:7997] + "…"
        transcript_lines.append(f"**{r}:** {c}")
    transcript_md = "\n\n".join(transcript_lines)

    tool_snippets = [
        (m.get("role"), (m.get("content") or "")[:2000])
        for m in prefix
        if (m.get("role") or "") == "tool"
    ]
    tools_note = ""
    if tool_snippets:
        tools_note = "\n\nTool messages in this thread (truncated):\n" + json.dumps(
            tool_snippets[-5:], ensure_ascii=False
        )

    other = _other_provider(selected_provider)
    alt_reply = _try_single_turn_reply(other, trigger_user or "")

    assess_key, assess_prov = _pick_assessor_key_and_provider()
    assess_client = get_llm_client(assess_prov)

    user_block = f"""**Currently selected provider (user setting):** {selected_provider}

**Transcript (user/assistant only, up to but not including the complaint):**

{transcript_md}
{tools_note}

**Last exchange under review:**
- User said: {(trigger_user or "(unknown)")[:12000]}
- Assistant replied: {(bad_assistant or "")[:12000]}

**Same user message run on `{other}` only (single turn, no chat history):**
{(alt_reply or "[Not available — API key missing or error]")[:16000]}
"""

    try:
        assessment = assess_client.chat(
            assess_key,
            user_block,
            None,
            None,
            ASSESSOR_SYSTEM,
        )
    except Exception as e:
        return {"ok": False, "error": f"Assessment failed: {e}"}

    text = (assessment or "").strip()
    return {
        "ok": True,
        "assessment": text,
        "alternate_provider": other,
        "alternate_reply": alt_reply,
        "assessor_provider": assess_prov,
        "selected_provider": selected_provider,
    }


def format_feedback_assessment_markdown(result: dict[str, Any]) -> str:
    """Turn run_feedback_assessment output into one assistant message for the chat UI."""
    if not result.get("ok"):
        return (result.get("error") or "Could not complete feedback review.").strip()

    parts = [
        "## Quality review\n\n",
        result.get("assessment") or "_No assessment text._",
    ]
    alt = result.get("alternate_reply")
    other = result.get("alternate_provider") or "other model"
    if alt and not str(alt).startswith("[Could not run"):
        parts.append(
            f"\n\n---\n\n## Same question on **{other}** (single turn, for comparison)\n\n{alt}"
        )
    elif alt:
        parts.append(f"\n\n---\n\n_{alt}_")
    return "".join(parts).strip()
