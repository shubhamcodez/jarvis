"""Post-turn memory write-back (Mem0-style: retrieve before, write after).

Heuristic extraction only — no extra LLM call. Durable first-person facts and
new chat windows are upserted. Ingest is incremental and debounced per chat.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger("jarvis.memory.writeback")

_FACT_HINTS = re.compile(
    r"(?i)\b("
    r"i (?:am|i'm|prefer|always|never|usually|work(?: at| on)?|live|use)|"
    r"my (?:name|timezone|editor|stack|goal|project)|"
    r"remember (?:that|this)|"
    r"don't (?:ever )?(?:mention|do)|"
    r"please (?:always|never)"
    r")\b"
)
_DEBOUNCE_SEC = 25.0
_LAST_INGEST: dict[str, float] = {}
_INGEST_LOCK = threading.Lock()
_TASKS: set = set()


def _looks_durable(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 12 or len(t) > 400:
        return False
    if t.startswith("/"):
        return False
    return bool(_FACT_HINTS.search(t))


def _should_ingest(chat_id: str, force: bool) -> bool:
    if not chat_id:
        return False
    if force:
        return True
    now = time.time()
    with _INGEST_LOCK:
        last = _LAST_INGEST.get(chat_id, 0.0)
        if now - last < _DEBOUNCE_SEC:
            return False
        return True


def _mark_ingested(chat_id: str) -> None:
    if not chat_id:
        return
    with _INGEST_LOCK:
        _LAST_INGEST[chat_id] = time.time()


def write_back_turn(
    *,
    chat_id: str,
    user_message: str,
    assistant_reply: str = "",
    openai_api_key: Optional[str] = None,
) -> dict:
    out = {"facts_added": 0, "chunks_added": 0, "ok": True, "ingested": False}
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
    if not _should_ingest(chat_id, force=bool(out["facts_added"])):
        return out
    try:
        from memory import get_memory_store
        from memory.ingest import ingest_chat

        store = get_memory_store()
        out["chunks_added"] = ingest_chat(store, openai_api_key, chat_id, persist=True, only_new=True)
        out["ingested"] = True
        _mark_ingested(chat_id)
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
    """Fire-and-forget write-back. Never blocks the caller."""
    rid = None
    try:
        from agents.run_control import current_run_id, is_cancelled

        rid = current_run_id()
        if rid and is_cancelled(rid):
            return
    except Exception:
        rid = None
    try:
        from config import get_openai_api_key

        try:
            key = get_openai_api_key()
        except ValueError:
            try:
                from config import get_llm_api_key

                key = get_llm_api_key()
            except Exception:
                key = None

        def _run(bound_rid=rid) -> None:
            try:
                from agents.run_control import is_cancelled

                if bound_rid and is_cancelled(bound_rid):
                    return
            except Exception:
                pass
            write_back_turn(
                chat_id=chat_id,
                user_message=user_message,
                assistant_reply=assistant_reply,
                openai_api_key=key,
            )

        try:
            import asyncio

            loop = asyncio.get_running_loop()

            async def _ago() -> None:
                await asyncio.to_thread(_run)

            task = loop.create_task(_ago())
            _TASKS.add(task)
            task.add_done_callback(_TASKS.discard)
        except RuntimeError:
            t = threading.Thread(target=_run, name="ada-writeback", daemon=True)
            t.start()
    except Exception as exc:
        logger.debug("schedule write-back skipped: %s", exc)
