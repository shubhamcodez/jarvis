"""Observe → ground → act → verify. One loop for every desktop goal."""
from __future__ import annotations

import base64
import sys
import time
from typing import Callable, Optional

from observability.guards import should_stop_streak

from .actions import execute_action
from .memory import WorkingMemory
from .perception import capture_screen, pixel_hash, resize_for_vision
from .verify import after_action_hash, expects_visual_change
from .vision import ask_desktop_action


def _cancelled(run_id: Optional[str]) -> bool:
    if not run_id:
        return False
    try:
        from agents.run_control import is_cancelled

        return is_cancelled(run_id)
    except Exception:
        return False


def run_computer_use_loop(
    goal: str,
    *,
    api_key: str,
    provider: str = "openai",
    max_steps: int = 25,
    duration_sec: Optional[float] = None,
    on_step: Optional[Callable] = None,
    run_id: Optional[str] = None,
) -> str:
    from agents.planning import get_plan

    mem = WorkingMemory(phase="plan")
    plan = get_plan(goal, "desktop", api_key, provider) or [goal]
    plan_text = "Plan:\n" + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(plan))
    if on_step:
        on_step(0, plan_text, "plan", plan_text, None, False, screenshot_base64=None)

    plat = "Windows" if sys.platform == "win32" else "macOS" if sys.platform == "darwin" else "Linux"
    deadline = time.time() + float(duration_sec) if duration_sec else None
    last_result: Optional[str] = None
    action_history: list[dict] = []
    trace: list[str] = [plan_text]
    plan_index = 0
    achieved = False

    for step in range(1, max_steps + 1):
        if _cancelled(run_id):
            trace.append("Stopped by user.")
            break
        remaining = (deadline - time.time()) if deadline else None
        if remaining is not None and remaining <= 0:
            trace.append("Time budget reached.")
            break

        shot = capture_screen()
        vis_png, vw, vh, sx, sy = resize_for_vision(shot.png, max_side=1280)
        vis_b64 = base64.b64encode(vis_png).decode("ascii")
        mem.remember_hash(pixel_hash(shot.png))
        current = plan[plan_index] if 0 <= plan_index < len(plan) else goal
        time_note = ""
        if remaining is not None:
            time_note = f"\nTime left: ~{int(max(0, remaining))}s. Do not stop early unless the goal is done."
        goal_block = (
            f"Platform: {plat}.\n"
            f"Overall user goal: {goal}\n"
            f"Current plan step ({plan_index + 1}/{len(plan)}): {current}\n"
            f'"done" only if the OVERALL goal is complete.{time_note}'
        )
        action = ask_desktop_action(
            api_key,
            provider,
            vis_b64,
            goal_block,
            step,
            last_result,
            vw,
            vh,
            memory_text=mem.prompt_block(),
        )
        thought = action.get("thought") or action.get("description") or action.get("action")
        desc = action.get("description") or action.get("action")
        act = (action.get("action") or "").lower()

        if act in ("click", "double_click", "right_click", "move") and action.get("x") is not None:
            mx = float(action["x"]) * sx
            my = float(action["y"]) * sy
            gx, gy = shot.to_mouse(mx, my)
            action["x"], action["y"] = gx, gy
        if act == "drag" and action.get("x") is not None and action.get("x2") is not None:
            a = shot.to_mouse(float(action["x"]) * sx, float(action["y"]) * sy)
            b = shot.to_mouse(float(action["x2"]) * sx, float(action["y2"]) * sy)
            action["x"], action["y"] = a
            action["x2"], action["y2"] = b

        trace.append(f"Step {step} — {thought}\n  Action: {desc}")

        if act == "done":
            if remaining is not None and remaining > 20:
                last_result = "Time remains on the goal; only use done if the user goal is actually finished."
                mem.notes.append("Ignored premature done while a time budget remains.")
                if on_step:
                    on_step(step, last_result, "wait", last_result, last_result, False, screenshot_base64=shot.b64)
                time.sleep(0.5)
                continue
            achieved = True
            if on_step:
                on_step(step, thought, "done", desc, None, True, screenshot_base64=shot.b64)
            break

        if act != "wait":
            action_history.append({"action": act, "thought": thought})
            if should_stop_streak(act, thought, action_history, streak_limit=4):
                if remaining is not None and remaining > 20:
                    last_result = "Repeated action; try a different approach."
                    action_history.clear()
                else:
                    trace.append("Loop guard: repeated action; stopping.")
                    if on_step:
                        on_step(
                            step, thought, "done", "Stopped (repeated action)", None, True, screenshot_base64=shot.b64
                        )
                    break

        before = pixel_hash(shot.png)
        result = execute_action(action)
        last_result = result
        if result:
            trace.append(f"  → {result}")
        mem.remember_action(act, result)

        after_h, after_shot = after_action_hash(settle_sec=0.28)
        if expects_visual_change(act) and before == after_h:
            mem.stuck += 1
            mem.notes.append("Last click/type did not change the screen; retry a different target.")
            last_result = (result or "") + " (no visible change)"
        else:
            mem.stuck = 0
            if act not in ("wait", "note", "analyze_chess") and plan_index < len(plan) - 1:
                plan_index = min(plan_index + 1, len(plan) - 1)
                mem.phase = f"plan {plan_index + 1}/{len(plan)}"

        if on_step:
            on_step(
                step,
                thought,
                action.get("action"),
                desc,
                last_result,
                False,
                screenshot_base64=getattr(after_shot, "b64", shot.b64),
            )

    if not achieved:
        trace.append(f"Stopped after {len(trace)} events (goal not marked done).")
    return f"Desktop task (goal: {goal}).\n\nAgent thought process:\n\n" + "\n\n".join(trace)
