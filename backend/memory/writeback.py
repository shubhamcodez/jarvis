"""Post-turn memory write-back (Mem0-style: retrieve before, write after).

Heuristic extraction only — no extra LLM call. Durable first-person facts and
the current chat are upserted into the persistent store.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("ada.memory.writeback")

_FACT_HINTS = re.compile(
    r"(?i)\b("
    r"i (?:am|i'm|prefer|always|never|usually|work(?: at| on)?|live|use)|"
    r"my (?:name|timezone|editor|stack|goal|project)|"
    r"remember (?:that|this)|"
    r"don't (?:ever )?(?:mention|do)|"
    r"please (?:always|never)"
    r")\b"
)


def _looks_durable(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 12 or len(t) > 400:
        return False
    if t.startswith("/"):
        return False
    return bool(_FACT_HINTS.search(t))


def write_back_turn(
    *,
    chat_id: str,
    user_message: str,
    assistant_reply: str = "",
    openai_api_key: Optional[str] = None,
) -> dict:
    """
    Persist this turn into episodic (vector) and semantic (facts) stores.
    Safe to call from a background thread. Failures are logged, never raised.
    """
    out = {"facts_added": 0, "chunks_added": 0, "ok": True}
    msg = (user_message or "").strip()
    try:
        if _looks_durable(msg):
            from memory.facts import add_fact

            add_fact(msg[:500], source="writeback", confidence=0.75)
            out["facts_added"] = 1
            try:
                from observability.metrics import incr

                incr("memory.writeback.facts")
            except Exception:
                pass
    except Exception as exc:
        logger.warning("fact write-back failed: %s", exc)
        out["ok"] = False
    if not chat_id or not openai_api_key:
        return out
    try:
        from memory import get_memory_store
        from memory.ingest import ingest_chat

        store = get_memory_store()
        out["chunks_added"] = ingest_chat(store, openai_api_key, chat_id)
        if hasattr(store, "persist"):
            store.persist()
        try:
            from observability.metrics import incr, observe

            incr("memory.writeback.ingest")
            observe("memory.writeback.chunks", float(out["chunks_added"]))
        except Exception:
            pass
    except Exception as exc:
        logger.warning("episodic write-back failed: %s", exc)
        out["ok"] = False
    return out


def schedule_write_back(
    *,
    chat_id: str,
    user_message: str,
    assistant_reply: str = "",
) -> None:
    """Fire-and-forget write-back after a successful turn."""
    try:
        import asyncio

        from config import get_openai_api_key

        try:
            key = get_openai_api_key()
        except ValueError:
            try:
                from config import get_llm_api_key

                key = get_llm_api_key()
            except Exception:
                key = None

        async def _run() -> None:
            await asyncio.to_thread(
                write_back_turn,
                chat_id=chat_id,
                user_message=user_message,
                assistant_reply=assistant_reply,
                openai_api_key=key,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            write_back_turn(
                chat_id=chat_id,
                user_message=user_message,
                assistant_reply=assistant_reply,
                openai_api_key=key,
            )
            return
        task = loop.create_task(_run())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except Exception as exc:
        logger.debug("schedule write-back skipped: %s", exc)


_TASKS: set = set()
