"""VLM calls for computer use: action choice, board locate, FEN read."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from agents.models import get_llm_client

_ACTION_SYSTEM = """You are Jarvis's computer-use controller. You see a screenshot and pick ONE next action.

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
        for m in reversed(list(re.finditer(r"\{[^{}]*\}", text, re.S))):
            try:
                out = json.loads(m.group(0))
                if isinstance(out, dict):
                    return out
            except json.JSONDecodeError:
                continue
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
    out = ask_vision_json(api_key, provider, image_b64, system, user, max_tokens=700)
    act = str(out.get("action") or "wait").strip().lower()
    out["action"] = act
    for key in ("x", "y", "x2", "y2"):
        if out.get(key) is None:
            continue
        try:
            v = float(out[key])
        except (TypeError, ValueError):
            out[key] = None
            continue
        if key in ("x", "x2"):
            out[key] = max(0, min(image_width - 1, v))
        else:
            out[key] = max(0, min(image_height - 1, v))
    return out


_CHESS_SCENE_SYSTEM = """You are looking at a screenshot that may show chess.com or lichess.

Reply with ONLY JSON:
{"phase":"lobby"|"ingame"|"over"|"other","board":{"x":int,"y":int,"w":int,"h":int}|null,"white_at_bottom":true,"our_side":"w"|"b"|null,"our_turn":true|false|null,"result":"1-0"|"0-1"|"1/2-1/2"|null,"click":{"x":int,"y":int,"description":str}|null}

Rules:
- phase lobby: home / play menu / seek / time-control. Set click to Play Online, New Game, Play, or the confirm button that starts a game — NOT ads or login.
- phase ingame: a live board is visible. board is the 8x8 board box in this image (pixels). white_at_bottom is true if White's pieces (or our light pieces) sit on the bottom rank. our_side is the color at the bottom. our_turn is true if it is that side to move.
- phase over: result banner / Game Over / Checkmate / Draw.
- click is only for lobby (or to dismiss a blocking dialog). Coordinates are in this image.
"""


_BOARD_FEN_SYSTEM = """The image is a chessboard with rank/file labels overlaid.
Read every square. Reply with ONLY JSON:
{"ranks":["........","........","........","........","........","........","........","........"],"side_to_move":"w"|"b"}

ranks has 8 strings, rank 8 first through rank 1, 8 characters each.
Use KQRBNP for white, kqrbnp for black, . for empty.
"""


def ask_chess_scene(
    api_key: str,
    provider: str,
    image_b64: str,
    image_width: int,
    image_height: int,
    goal: str,
) -> dict[str, Any]:
    user = (
        f"Image is {image_width}×{image_height}. Goal: {goal}\n"
        "Locate the chess UI and return the JSON object."
    )
    out = ask_vision_json(api_key, provider, image_b64, _CHESS_SCENE_SYSTEM, user, max_tokens=500)
    board = out.get("board")
    if isinstance(board, dict):
        for k in ("x", "y", "w", "h"):
            try:
                board[k] = int(float(board[k]))
            except (TypeError, ValueError, KeyError):
                board = None
                break
        out["board"] = board
    click = out.get("click")
    if isinstance(click, dict) and click.get("x") is not None and click.get("y") is not None:
        try:
            click["x"] = max(0, min(image_width - 1, float(click["x"])))
            click["y"] = max(0, min(image_height - 1, float(click["y"])))
        except (TypeError, ValueError):
            out["click"] = None
    else:
        out["click"] = None
    return out


def ask_board_placement(
    api_key: str,
    provider: str,
    image_b64: str,
    side_hint: str = "w",
) -> dict[str, Any]:
    user = f"side_to_move hint: {side_hint}. Reply with ONLY the JSON object."
    return ask_vision_json(api_key, provider, image_b64, _BOARD_FEN_SYSTEM, user, max_tokens=600)
