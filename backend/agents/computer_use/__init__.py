"""Computer-use substrate: observe → ground → act → verify."""

from .budget import parse_duration, steps_for_budget
from .loop import run_computer_use_loop

__all__ = [
    "parse_duration",
    "run_computer_use_loop",
    "steps_for_budget",
]
