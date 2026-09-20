"""VLM calls for computer use: action choice, board locate, FEN read."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from agents.models import get_llm_client

_ACTION_SYSTEM = """You are Ada's computer-use controller. You see a screenshot and pick ONE next action.

You operate in an observe → ground → act → verify loop:
- Ground: name the exact on-screen target (button label, icon, square) before clicking.
- Prefer keyboard shortcuts when they are reliable (Ctrl+T, Enter, Win+S).
- Use "wait" when the UI is loading or you are waiting for an opponent / animation.
- Use "drag" only when click-click is wrong (sliders, dragging a piece).
- "note": report what you see to the user (no mouse). Use "text".
- "analyze_chess": optional engine check AFTER you yourself read a position from the screenshot. Pass "fen" or "ranks" (8 rows, rank 8 first) and "side_to_move".
- "done" ONLY if the FULL user goal is already complete on screen.

{coord_rule}

Reply with ONLY JSON:
{{"action":"click"|"double_click"|"right_click"|"type"|"press"|"scroll"|"hotkey"|"wait"|"drag"|"move"|"note"|"analyze_chess"|"done","x":null,"y":null,"x2":null,"y2":null,"text":null,"key":null,"keys":null,"scroll_amount":null,"seconds":null,"fen":null,"ranks":null,"side_to_move":null,"description":"...","thought":"what you see + why"}}
"""


def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {"action": "wait", "thought": text[:400]}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                out = json.loads(m.group(0))
                if isinstance(out, dict):
                    return out
            except json.JSONDecodeError:
                pass
        return {"action": "wait", "seconds": 1, "description": "parse_error", "thought": text[:400]}


def _create(provider: str, api_key: str, model: str, messages: list, max_tokens: int):
    mod = get_llm_client(provider)
    client = mod._client(api_key)
    kwargs = {}
    if hasattr(mod, "chat_completion_limit_kwargs"):
        kwargs = mod.chat_completion_limit_kwargs(provider, model, max_tokens)
    else:
        kwargs = {"max_tokens": max_tokens}
    return client.chat.completions.create(model=model, messages=messages, **kwargs)


def _vision_model(provider: str) -> str:
    mod = get_llm_client(provider)
    return getattr(mod, "VISION_MODEL", None) or getattr(mod, "CHAT_MODEL", "gpt-4o")


def ask_vision_json(
    api_key: str,
    provider: str,
    image_b64: str,
    system: str,
    user: str,
    max_tokens: int = 900,
) -> dict[str, Any]:
    model = _vision_model(provider)
    resp = _create(
        provider,
        api_key,
        model,
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ],
        max_tokens,
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_json(raw)


def ask_desktop_action(
    api_key: str,
    provider: str,
    image_b64: str,
    goal: str,
    step: int,
    last_result: Optional[str],
    image_width: int,
    image_height: int,
    memory_text: str = "",
) -> dict[str, Any]:
    coord_rule = (
        f"The image is exactly {image_width}×{image_height} pixels. "
        f"Click x in [0,{image_width - 1}], y in [0,{image_height - 1}]. (0,0) is top-left. "
        "Click the CENTER of the target."
    )
    system = _ACTION_SYSTEM.format(coord_rule=coord_rule)
    user = f"Goal: {goal}\nStep: {step}\n"
    if last_result:
        user += f"Last result: {last_result}\n"
    if memory_text:
        user += f"Working memory:\n{memory_text}\n"
    user += "Reply with ONLY the JSON object."
    return ask_vision_json(api_key, provider, image_b64, system, user, max_tokens=700)
