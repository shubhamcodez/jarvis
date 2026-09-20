"""OpenAI API: chat, classification, vision for desktop agent."""
import json
import os
import re
from typing import Any, Optional

from openai import OpenAI

# Default to a reasoning-capable model (see https://developers.openai.com/docs/guides/reasoning).
# Override: OPENAI_CHAT_MODEL, OPENAI_VISION_MODEL (e.g. gpt-5-mini, o4-mini, gpt-4o).
_DEFAULT_CHAT = "gpt-5.4"
_DEFAULT_VISION = "gpt-5.4"

CHAT_MODEL = (os.environ.get("OPENAI_CHAT_MODEL") or _DEFAULT_CHAT).strip() or _DEFAULT_CHAT
VISION_MODEL = (os.environ.get("OPENAI_VISION_MODEL") or _DEFAULT_VISION).strip() or _DEFAULT_VISION


def _is_openai_reasoning_family(model: str) -> bool:
    """Models that use max_completion_tokens (not max_tokens) on Chat Completions."""
    m = (model or "").lower()
    return bool(
        m.startswith("gpt-5")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    )


def chat_completion_limit_kwargs(
    provider: str,
    model: str,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """Map max_tokens → max_completion_tokens for OpenAI reasoning / GPT-5 family."""
    if max_tokens is None:
        return {}
    if (provider or "").lower() == "openai" and _is_openai_reasoning_family(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def should_omit_temperature(provider: str, model: str) -> bool:
    """o-series often rejects non-default temperature."""
    if (provider or "").lower() != "openai":
        return False
    m = (model or "").lower()
    return m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def _provider_openai() -> str:
    return "openai"


def _build_messages(
    message: str,
    attachment_paths: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
    system_content: Optional[str] = None,
) -> list[dict]:
    """Build API messages list: optional system, then history or single user message."""
    messages = []
    if system_content and system_content.strip():
        messages.append({"role": "system", "content": system_content.strip()})
    if history:
        for m in history:
            role = (m.get("role") or "user").strip().lower()
            if role not in ("user", "assistant", "system"):
                role = "user"
            content = (m.get("content") or "").strip()
            messages.append({"role": role, "content": content or " "})
        if attachment_paths and messages and messages[-1].get("role") == "user":
            messages[-1]["content"] = _user_content(messages[-1]["content"], attachment_paths)
    else:
        messages.append({"role": "user", "content": _user_content(message, attachment_paths)})
    return messages


def chat(
    api_key: str,
    message: str,
    attachment_paths: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
    system_content: Optional[str] = None,
) -> str:
    """Chat: single message or full history. Optional system_content (e.g. memory context)."""
    client = _client(api_key)
    messages = _build_messages(message, attachment_paths, history, system_content)
    resp = client.chat.completions.create(model=CHAT_MODEL, messages=messages)
    return (resp.choices[0].message.content or "No response.").strip()


def _user_content(message: str, attachment_paths: Optional[list[str]] = None) -> str:
    """Build user content string for chat (shared by chat and chat_stream)."""
    message = (message or "").strip()
    if attachment_paths:
        parts = []
        for path in attachment_paths:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                with open(path, "rb") as f:
                    content = f.read().decode("utf-8", errors="replace")
            name = path.split("/")[-1].split("\\")[-1] or "file"
            parts.append(f"[Contents of {name}]\n{content}")
        if parts:
            body = "\n\n".join(parts)
            if message:
                return f"{message}\n\nAttachments:\n{body}"
            return f"Please summarize or answer based on the attached documents.\n\n{body}"
    return message or "Hello."


def chat_stream(
    api_key: str,
    message: str,
    attachment_paths: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
    system_content: Optional[str] = None,
):
    """Chat with streaming: single message or full history. Optional system_content."""
    client = _client(api_key)
    messages = _build_messages(message, attachment_paths, history, system_content)
    stream = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def classify_task(api_key: str, user_message: str) -> dict:
    """Classify user message as task (agent) or normal chat. Returns {is_task, goal}."""
    user_message = (user_message or "").strip()
    if not user_message:
        return {"is_task": False, "goal": None}

    system = """You are a classifier. The user is talking to an assistant that can either (1) chat normally (answer questions, summarize, discuss) or (2) perform actions on the computer (open URLs, navigate, click, fill forms, etc.).

If the user is clearly asking the assistant to DO something on the computer (e.g. "open example.com", "go to google and search for X", "navigate to that page"), reply with a JSON object only, no other text: {"is_task": true, "goal": "one clear sentence describing the task"}.

Otherwise (general question, chat, "what is X", "summarize this", "hello", or unclear), reply with: {"is_task": false, "goal": null}.

Output ONLY the JSON object, no markdown or explanation."""

    client = _client(api_key)
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        **chat_completion_limit_kwargs(_provider_openai(), CHAT_MODEL, 512),
    )
    raw = (resp.choices[0].message.content or "").strip()
    # Strip ```json ... ```
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()
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
    """Vision: screenshot + goal -> next action (mouse + keyboard via pyautogui). Pass image_width/height for precise click coordinates."""
    coord_rule = (
        f"- The screenshot is exactly {image_width}×{image_height} pixels. For clicks, return \"x\" and \"y\" as integer pixel coordinates in this system: x from 0 to {image_width - 1}, y from 0 to {image_height - 1}. (0,0) is top-left. Give the center of the element to click."
        if (image_width and image_height and image_width > 0 and image_height > 0)
        else "- Coordinates: (0,0) is top-left. x increases right, y increases down. Give pixel coordinates for the center of the element to click."
    )
    system = """You control the user's desktop by looking at a screenshot and deciding the next mouse/keyboard action.

RULES:
- Goal text may include a "Current plan step" line as a hint for this turn—but action **"done"** means the **entire user goal** (what they ultimately want, e.g. "open a new tab in Chrome") is **fully** achieved. Do NOT use "done" just because one plan sub-step looks satisfied (e.g. Chrome is already focused when the user still wants a **new tab**).
- If the overall goal requires a **new browser tab**: after the browser is active, use **hotkey** (Windows/Linux: keys ["ctrl","t"]; macOS: keys ["command","t"]) OR **click** the "+" new-tab control on the tab bar—never skip to "done" until a new tab actually exists or you have sent the hotkey/click.
- On Windows: taskbar is usually at the BOTTOM. Icons (Chrome, etc.) are on the taskbar.
- If the target app is NOT in the foreground: click its taskbar icon (click + x,y) or use the taskbar search (click search box, then type, then click result).
"""
    system += coord_rule + """

- Reply with ONLY a JSON object, no markdown or other text. Include "thought": a 1–2 sentence explanation. Format:
{"action": "click"|"double_click"|"right_click"|"type"|"press"|"scroll"|"hotkey"|"wait"|"drag"|"move"|"done", "x": number or null, "y": number or null, "x2": number or null, "y2": number or null, "text": string or null, "key": string or null, "scroll_amount": number or null, "seconds": number or null, "keys": ["ctrl","t"] or null, "description": "what you're doing", "thought": "what you see and why you're doing this"}
- "press": one key name in "key" (e.g. "enter", "tab", "esc"). Omit x/y unless also clicking.
- "hotkey": set "keys" to modifiers+key (e.g. ["ctrl","t"], ["command","t"], ["alt","f4"]). Omit x/y.
- Use "done" ONLY when the **full** user goal is satisfied. For "done", set thought to a brief summary.
- Prefer **press**/**hotkey** for Enter, shortcuts, and dialogs; use **click** to focus controls first when needed."""

    text = f"Goal: {goal}. Step {step}. "
    if last_result:
        text += f"Last action result: {last_result} "
    text += "Reply with ONLY the JSON object."

    client = _client(api_key)
    resp = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ],
            },
        ],
        **chat_completion_limit_kwargs(_provider_openai(), VISION_MODEL, 2048),
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"action": "done", "description": "Parse error", "thought": raw or "Unknown"}
