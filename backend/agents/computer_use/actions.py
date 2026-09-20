"""Constrained motor primitives. Coordinates are OS mouse pixels after mapping."""
from __future__ import annotations

import sys
import time
from typing import Any, Optional

try:
    import pyautogui

    HAS_PYAUTOGUI = True
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.04
except ImportError:
    HAS_PYAUTOGUI = False
    pyautogui = None  # type: ignore


def _normalize_key(key: str) -> str:
    k = (key or "").strip().lower()
    aliases = {
        "return": "enter",
        "escape": "esc",
        "del": "delete",
        "ins": "insert",
        "pgup": "pageup",
        "pgdn": "pagedown",
    }
    return aliases.get(k, k)


def _xy(action: dict[str, Any], *keys: str) -> Optional[tuple[int, int]]:
    if len(keys) == 2:
        x, y = action.get(keys[0]), action.get(keys[1])
    else:
        x, y = action.get("x"), action.get("y")
    if x is None or y is None:
        return None
    return int(round(float(x))), int(round(float(y)))


def execute_action(action: dict[str, Any]) -> Optional[str]:
    """Run one grounded action. Returns a result string or None for done."""
    act = (action.get("action") or "").strip().lower()
    if act in ("done", ""):
        return None
    if act == "note":
        return str(action.get("text") or action.get("description") or thought_or_empty(action))[:4000]
    if act == "analyze_chess":
        from .chess import analyze_position

        return analyze_position(action)
    if not HAS_PYAUTOGUI:
        return "pyautogui not installed; skipping execution"
    if act == "wait":
        sec = action.get("seconds")
        if sec is None:
            sec = action.get("scroll_amount") or 1
        try:
            t = max(0.15, min(8.0, float(sec)))
        except (TypeError, ValueError):
            t = 1.0
        time.sleep(t)
        return f"Waited {t:.1f}s"
    if act == "click":
        pt = _xy(action)
        if not pt:
            return "Click requires x and y"
        pyautogui.click(*pt)
        return f"Clicked at {pt}"
    if act == "double_click":
        pt = _xy(action)
        if not pt:
            return "double_click requires x and y"
        pyautogui.doubleClick(*pt)
        return f"Double-clicked at {pt}"
    if act == "right_click":
        pt = _xy(action)
        if not pt:
            return "right_click requires x and y"
        pyautogui.rightClick(*pt)
        return f"Right-clicked at {pt}"
    if act == "move":
        pt = _xy(action)
        if not pt:
            return "move requires x and y"
        pyautogui.moveTo(*pt, duration=0.12)
        return f"Moved cursor to {pt}"
    if act == "drag":
        a = _xy(action)
        b = _xy(action, "x2", "y2")
        if not a or not b:
            return "drag requires x,y and x2,y2"
        pyautogui.moveTo(*a, duration=0.08)
        pyautogui.dragTo(b[0], b[1], duration=0.22, button="left")
        return f"Dragged {a} → {b}"
    if act == "type":
        text = str(action.get("text") or "")
        if any(ord(ch) > 127 for ch in text):
            try:
                import pyperclip

                prev = ""
                try:
                    prev = pyperclip.paste()
                except Exception:
                    prev = ""
                pyperclip.copy(text)
                if sys.platform == "darwin":
                    pyautogui.hotkey("command", "v")
                else:
                    pyautogui.hotkey("ctrl", "v")
                time.sleep(0.05)
                try:
                    pyperclip.copy(prev)
                except Exception:
                    pass
                return f"Pasted: {text[:80]}"
            except Exception:
                pyautogui.write(text, interval=0.02)
                return f"Typed (fallback): {text[:80]}"
        pyautogui.write(text, interval=0.02)
        return f"Typed: {text}"
    if act == "press":
        key = action.get("key")
        if key is None or str(key).strip() == "":
            return 'press requires "key" (e.g. enter, tab, esc, space)'
        nk = _normalize_key(str(key))
        try:
            pyautogui.press(nk)
        except Exception as e:
            return f"press failed for key {nk!r}: {e}"
        return f"Pressed key: {nk}"
    if act == "scroll":
        amount = action.get("scroll_amount") or 3
        try:
            n = int(amount)
        except (TypeError, ValueError):
            n = 3
        pyautogui.scroll(-n)
        return f"Scrolled {n}"
    if act == "hotkey":
        keys = action.get("keys")
        if not isinstance(keys, list) or not keys:
            return 'hotkey requires non-empty keys array, e.g. ["ctrl","t"]'
        normalized = []
        for k in keys:
            s = str(k).strip().lower()
            if s in ("cmd", "command", "meta"):
                s = "command" if sys.platform == "darwin" else "win"
            normalized.append(s)
        pyautogui.hotkey(*normalized)
        return f"Hotkey: {'+'.join(normalized)}"
    return f"Unknown action: {act}"


def thought_or_empty(action: dict[str, Any]) -> str:
    return str(action.get("thought") or "")


def click_screen(x: int, y: int) -> str:
    return execute_action({"action": "click", "x": x, "y": y}) or f"Clicked at ({x}, {y})"


def drag_screen(x1: int, y1: int, x2: int, y2: int) -> str:
    return (
        execute_action({"action": "drag", "x": x1, "y": y1, "x2": x2, "y2": y2})
        or f"Dragged ({x1},{y1})→({x2},{y2})"
    )
