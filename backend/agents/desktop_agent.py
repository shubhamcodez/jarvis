"""Desktop agent: general computer-use (screenshot → ground → act → verify)."""
from typing import Optional

from agents.computer_use.actions import execute_action as _execute
from agents.computer_use.budget import implied_duration, parse_duration, steps_for_budget
from agents.computer_use.loop import run_computer_use_loop
from agents.computer_use.perception import capture_screen


def capture_screen_base64() -> str:
    return capture_screen().b64


def capture_screen_with_size() -> tuple[str, int, int]:
    shot = capture_screen()
    return shot.b64, shot.width, shot.height


def execute_action(action: dict) -> Optional[str]:
    return _execute(action)


def run_desktop_agent(
    goal: str,
    max_steps: int = 25,
    on_step=None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    duration_sec: Optional[float] = None,
    run_id: Optional[str] = None,
) -> str:
    if api_key is None:
        from config import get_llm_api_key

        api_key = get_llm_api_key()

    from agents.hitl import require_desktop_armed

    armed_err = require_desktop_armed()
    if armed_err:
        if on_step:
            on_step(0, armed_err, "permission", armed_err, None, True, screenshot_base64=None)
        return armed_err

    if duration_sec is None:
        duration_sec = parse_duration(goal) or implied_duration(goal)
    if duration_sec:
        max_steps = max(max_steps, steps_for_budget(duration_sec, max_steps))

    return run_computer_use_loop(
        goal,
        api_key=api_key,
        provider=provider,
        max_steps=max_steps,
        duration_sec=duration_sec,
        on_step=on_step,
        run_id=run_id,
    )
