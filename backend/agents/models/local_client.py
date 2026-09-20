"""Local Hugging Face / GGUF chat client with an OpenAI-shaped surface."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Optional

from agents.local_models import generate_chat, loaded_model_id

CHAT_MODEL = "local"
VISION_MODEL = "local"


def chat_completion_limit_kwargs(
    provider: str,
    model: str,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    if max_tokens is None:
        return {}
    return {"max_tokens": max_tokens}


def should_omit_temperature(provider: str, model: str) -> bool:
    return False


def _flatten_user_content(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
            elif isinstance(p, dict) and p.get("type") == "image_url":
                parts.append("[image omitted — local model is text-only]")
        return "\n".join(x for x in parts if x)
    return str(content or "")


def _normalize_messages(
    message: str,
    attachment_paths: Optional[list[str]],
    history: Optional[list[dict]],
    system_content: Optional[str],
) -> list[dict[str, str]]:
    from agents.models.openai_client import _user_content

    msgs: list[dict[str, str]] = []
    if system_content and system_content.strip():
        msgs.append({"role": "system", "content": system_content.strip()})
    if history:
        for m in history:
            role = (m.get("role") or "user").strip().lower()
            if role not in ("user", "assistant", "system"):
                role = "user"
            msgs.append({"role": role, "content": (m.get("content") or "").strip() or " "})
        if attachment_paths and msgs and msgs[-1].get("role") == "user":
            msgs[-1]["content"] = _user_content(msgs[-1]["content"], attachment_paths)
    else:
        msgs.append({"role": "user", "content": _user_content(message, attachment_paths)})
    return msgs


def _complete(messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
    chunks = list(generate_chat(messages, max_tokens=max_tokens, stream=False))
    return "".join(chunks).strip() or "No response."


class _Completions:
    def create(self, model: str = "local", messages: Optional[list] = None, stream: bool = False, **kwargs: Any):
        max_tokens = int(kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 1024)
        norm: list[dict[str, str]] = []
        for m in messages or []:
            role = (m.get("role") or "user") if isinstance(m, dict) else "user"
            content = m.get("content") if isinstance(m, dict) else str(m)
            norm.append({"role": role, "content": _flatten_user_content(content)})
        if stream:
            return _stream_chunks(norm, max_tokens)
        text = _complete(norm, max_tokens=max_tokens)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), delta=SimpleNamespace(content=text))]
        )


class _Chat:
    def __init__(self) -> None:
        self.completions = _Completions()


class _LocalClient:
    def __init__(self) -> None:
        self.chat = _Chat()


def _client(api_key: str) -> _LocalClient:
    return _LocalClient()


def chat(
    api_key: str,
    message: str,
    attachment_paths: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
    system_content: Optional[str] = None,
) -> str:
    return _complete(_normalize_messages(message, attachment_paths, history, system_content))


def chat_stream(
    api_key: str,
    message: str,
    attachment_paths: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
    system_content: Optional[str] = None,
):
    msgs = _normalize_messages(message, attachment_paths, history, system_content)
    yield from generate_chat(msgs, stream=True)


def _stream_chunks(messages: list[dict[str, str]], max_tokens: int):
    for delta in generate_chat(messages, max_tokens=max_tokens, stream=True):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=delta))])


def classify_task(api_key: str, user_message: str) -> dict:
    user_message = (user_message or "").strip()
    if not user_message:
        return {"is_task": False, "goal": None}
    system = (
        "You are a classifier. Reply with JSON only: "
        '{"is_task": true, "goal": "..."} if the user wants a computer action, '
        'else {"is_task": false, "goal": null}.'
    )
    raw = _complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        max_tokens=256,
    )
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw).strip()
    try:
        out = json.loads(raw)
        return {"is_task": bool(out.get("is_task")), "goal": out.get("goal")}
    except json.JSONDecodeError:
        return {"is_task": False, "goal": None}


def vision_desktop_action(
    api_key: str,
    image_base64: str,
    goal: str,
    step: int,
    last_result: Optional[str] = None,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> dict:
    mid = loaded_model_id() or "local"
    return {
        "action": "done",
        "description": "Local text model cannot see the screen",
        "thought": (
            f"The loaded local model ({mid}) is text-only. "
            "Switch to OpenAI or xAI in Settings for desktop vision."
        ),
        "x": None,
        "y": None,
        "text": None,
        "key": None,
        "scroll_amount": None,
        "keys": None,
    }


def refresh_chat_model_label() -> None:
    global CHAT_MODEL
    CHAT_MODEL = loaded_model_id() or "local"
    global VISION_MODEL
    VISION_MODEL = CHAT_MODEL
