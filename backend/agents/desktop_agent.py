"""Desktop agent: screenshot + vision → pyautogui. Controls the real mouse cursor and keyboard (clicks, keys, hotkeys)."""
import base64
import sys
from typing import Optional

from agents.models import get_llm_client
from observability.guards import should_stop_streak

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


def capture_screen_base64() -> str:
    """Capture screen as PNG base64."""
    if not HAS_MSS:
        raise RuntimeError("mss not installed. pip install mss")
    with mss.mss() as sct:
        mon = sct.monitors[0]
        shot = sct.grab(mon)
        img = mss.tools.to_png(shot.rgb, shot.size)
    return base64.b64encode(img).decode("ascii")


def capture_screen_with_size() -> tuple[str, int, int]:
    """Capture screen as PNG base64 and return (base64_string, width, height) for precise coordinate mapping."""
    if not HAS_MSS:
        raise RuntimeError("mss not installed. pip install mss")
    with mss.mss() as sct:
        mon = sct.monitors[0]
        width = mon["width"]
        height = mon["height"]
        shot = sct.grab(mon)
        img = mss.tools.to_png(shot.rgb, shot.size)
    return base64.b64encode(img).decode("ascii"), width, height


def _override_premature_done_for_new_tab(goal: str, execution_step: int) -> bool:
    """Vision often returns done on step 1 when Chrome is focused but no new tab was opened."""
    if execution_step != 1:
        return False
    g = (goal or "").lower()
    return "new tab" in g or "another tab" in g or "extra tab" in g


def _normalize_press_key(key: str) -> str:
    k = (key or "").strip().lower()
    aliases = {"return": "enter", "escape": "esc", "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown"}
    return aliases.get(k, k)


def execute_action(action: dict) -> Optional[str]:
    """Execute one desktop action: mouse (cursor) or keyboard. Returns result message or None."""
    if not HAS_PYAUTOGUI:
        return "pyautogui not installed; skipping execution"
    act = (action.get("action") or "").lower()
    if act == "done":
        return None
    if act == "click":
        x = action.get("x")
        y = action.get("y")
        if x is None or y is None:
            return "Click requires x and y coordinates"
        x = int(round(float(x)))
        y = int(round(float(y)))
        pyautogui.click(x, y)
        return f"Clicked at ({x}, {y})"
    if act == "double_click":
        x = action.get("x")
        y = action.get("y")
        if x is None or y is None:
            return "double_click requires x and y"
        x = int(round(float(x)))
        y = int(round(float(y)))
        pyautogui.doubleClick(x, y)
        return f"Double-clicked at ({x}, {y})"
    if act == "right_click":
        x = action.get("x")
        y = action.get("y")
        if x is None or y is None:
            return "right_click requires x and y"
        x = int(round(float(x)))
        y = int(round(float(y)))
        pyautogui.rightClick(x, y)
        return f"Right-clicked at ({x}, {y})"
    if act == "type":
        text = action.get("text") or ""
        pyautogui.write(text, interval=0.02)
        return f"Typed: {text}"
    if act == "press":
        key = action.get("key")
        if key is None or str(key).strip() == "":
            return "press requires \"key\" (e.g. enter, tab, esc, space, up, down)"
        nk = _normalize_press_key(str(key))
        try:
            pyautogui.press(nk)
        except Exception as e:
            return f"press failed for key {nk!r}: {e}"
        return f"Pressed key: {nk}"
    if act == "scroll":
        amount = action.get("scroll_amount") or 3
        pyautogui.scroll(-amount)  # pyautogui: positive = up
        return f"Scrolled {amount}"
    if act == "hotkey":
        keys = action.get("keys")
        if not isinstance(keys, list) or len(keys) == 0:
            return "hotkey requires non-empty keys array, e.g. [\"ctrl\",\"t\"]"
        normalized = []
        for k in keys:
            s = str(k).strip().lower()
            if s in ("cmd", "command", "meta"):
                s = "command" if sys.platform == "darwin" else "win"
            normalized.append(s)
        pyautogui.hotkey(*normalized)
        return f"Hotkey: {'+'.join(normalized)}"
    return f"Unknown action: {act}"


def run_desktop_agent(
    goal: str,
    max_steps: int = 10,
    on_step=None,
    api_key: Optional[str] = None,
    provider: str = "openai",
) -> str:
    """
    Run desktop agent: plan steps first, then screenshot -> vision -> execute -> evaluate.
    Decides retry / next / back based on outcome. on_step(step, ...) with step 0 = plan.
    """
    if api_key is None:
        from config import get_llm_api_key
        api_key = get_llm_api_key()

    from agents.hitl import require_desktop_armed
    from agents.planning import evaluate_step_outcome, get_plan

    armed_err = require_desktop_armed()
    if armed_err:
        if on_step:
            on_step(0, armed_err, "permission", armed_err, None, True, screenshot_base64=None)
        return armed_err

    client = get_llm_client(provider)
    trace = []
    last_result: Optional[str] = None
    achieved = False
    action_history = []

    # Plan first
    plan = get_plan(goal, "desktop", api_key, provider)
    if not plan:
        plan = [goal]
    plan_text = "Plan:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan))
    trace.append(plan_text)
    if on_step:
        on_step(0, plan_text, "plan", plan_text, None, False, screenshot_base64=None)

    plan_index = 0
    retry_count = 0
    max_retries_same_step = 2
    execution_step = 1

    while execution_step <= max_steps:
        image_b64, screen_width, screen_height = capture_screen_with_size()
        current_plan_step = plan[plan_index] if 0 <= plan_index < len(plan) else ""
        plat = (
            "Windows"
            if sys.platform == "win32"
            else "macOS"
            if sys.platform == "darwin"
            else "Linux"
        )
        goal_with_plan = (
            f"Platform: {plat}.\n"
            f"Overall user goal: {goal}\n"
            f"Current plan step ({plan_index + 1}/{len(plan)}) — do this next unless the overall goal is already fully done: {current_plan_step}\n"
            f"Remember: action \"done\" only if the OVERALL user goal is complete (not just this plan line)."
        )
        action = client.vision_desktop_action(
            api_key, image_b64, goal_with_plan, execution_step, last_result,
            image_width=screen_width, image_height=screen_height,
        )

        thought = action.get("thought") or action.get("description") or action.get("action")
        desc = action.get("description") or action.get("action")
        act = (action.get("action") or "").lower()

        trace.append(f"Step {execution_step} (plan {plan_index + 1}/{len(plan)}) — Thought: {thought}\n  Action: {desc}")

        if act == "done" and _override_premature_done_for_new_tab(goal, execution_step):
            keys = ["command", "t"] if sys.platform == "darwin" else ["ctrl", "t"]
            action = {
                "action": "hotkey",
                "keys": keys,
                "thought": "Model returned done too early for a new-tab goal; opening tab via keyboard.",
                "description": "New tab (Ctrl+T / Cmd+T)",
            }
            thought = action["thought"]
            desc = action["description"]
            act = "hotkey"
            trace[-1] = (
                f"Step {execution_step} (plan {plan_index + 1}/{len(plan)}) — Thought: {thought}\n"
                f"  Action: {desc} (corrected from premature done)"
            )

        if act == "done":
            trace.append("Goal achieved.")
            achieved = True
            if on_step:
                on_step(execution_step, thought, "done", desc, None, True, screenshot_base64=image_b64)
            break

        action_history.append({"action": act, "thought": thought})
        if should_stop_streak(act, thought, action_history, streak_limit=3):
            trace.append("Loop guard: repeated action; stopping.")
            if on_step:
                on_step(execution_step, thought, "done", "Stopped (repeated action)", None, True, screenshot_base64=image_b64)
            break

        result = execute_action(action)
        if result:
            last_result = result
            trace.append(f"  → {result}")

        if on_step:
            on_step(execution_step, thought, action.get("action"), desc, last_result, False, screenshot_base64=image_b64)

        # Evaluate outcome and decide retry / next / back
        eval_result = evaluate_step_outcome(
            goal,
            current_plan_step,
            last_result,
            plan,
            plan_index,
            api_key,
            provider,
        )
        decision = (eval_result.get("decision") or "next").lower()
        reason = eval_result.get("reason") or ""

        if decision == "retry":
            retry_count += 1
            if retry_count > max_retries_same_step:
                retry_count = 0
                plan_index = min(plan_index + 1, len(plan) - 1)
                trace.append(f"Retries exhausted; moving to next plan step. {reason}")
            else:
                trace.append(f"Retrying same step ({retry_count}/{max_retries_same_step}). {reason}")
        elif decision == "next":
            retry_count = 0
            plan_index += 1
            if plan_index >= len(plan):
                trace.append("Plan completed.")
                achieved = True
                if on_step:
                    on_step(execution_step, "Plan completed", "done", "Plan completed", None, True, screenshot_base64=image_b64)
                break
            trace.append(f"Next plan step: {plan[plan_index]}. {reason}")
        else:  # back
            retry_count = 0
            plan_index = max(0, plan_index - 1)
            trace.append(f"Going back to plan step {plan_index + 1}. {reason}")

        execution_step += 1

    if not achieved:
        trace.append(f"Stopped after {execution_step - 1} steps (goal not yet achieved).")

    return f"Desktop task (goal: {goal}).\n\nAgent thought process:\n\n" + "\n\n".join(trace)
