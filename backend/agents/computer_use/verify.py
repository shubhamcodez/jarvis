"""Action-effect verification without another LLM call."""
from __future__ import annotations

import time
from typing import Optional

from .perception import capture_screen, hashes_close, pixel_hash


def after_action_hash(*, settle_sec: float = 0.35) -> tuple[str, object]:
    time.sleep(max(0.05, settle_sec))
    shot = capture_screen()
    return pixel_hash(shot.png), shot


def effect_happened(before: Optional[str], after: Optional[str], *, expect_change: bool) -> bool:
    same = hashes_close(before, after)
    return (not same) if expect_change else same


def expects_visual_change(action: str) -> bool:
    return (action or "").lower() in {
        "click",
        "double_click",
        "right_click",
        "type",
        "press",
        "hotkey",
        "scroll",
        "drag",
    }
