"""
After each successful conversation turn: optionally run eval case generation and optimization
suggestions in the background. Never applies code—only appends eval cases and writes
optimization_stats.json (prompt/code suggestions for humans).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

logger = logging.getLogger("jarvis.observability")

_lock = threading.Lock()
_last_eval_gen = 0.0
_last_opt = 0.0
_TASKS: set[asyncio.Task] = set()


def _getenv(name_ada: str, default: str, name_legacy: str | None = None) -> str:
    v = os.environ.get(name_ada)
    if v is not None and str(v).strip() != "":
        return str(v)
    if name_legacy:
        v = os.environ.get(name_legacy)
        if v is not None and str(v).strip() != "":
            return str(v)
    return default


def _env_bool(name_ada: str, default: bool, name_legacy: str | None = None) -> bool:
    v = _getenv(name_ada, "", name_legacy)
    if v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _env_float(name_ada: str, default: float, name_legacy: str | None = None) -> float:
    v = _getenv(name_ada, "", name_legacy)
    if v.strip() == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _env_int(name_ada: str, default: int, name_legacy: str | None = None) -> int:
    v = _getenv(name_ada, "", name_legacy)
    if v.strip() == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def schedule_post_turn_observability() -> None:
    """
    Fire-and-forget background work after a trace has been written for this turn.
    Respects cooldowns and ADA_AUTO_* env vars (JARVIS_AUTO_* still honored).
    """
    if not _env_bool(
        "ADA_AUTO_OBSERVABILITY", True, "JARVIS_AUTO_OBSERVABILITY"
    ):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_post_turn_observability_task())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _post_turn_observability_task() -> None:
    await asyncio.to_thread(_run_post_turn_sync)


def _run_post_turn_sync() -> None:
    global _last_eval_gen, _last_opt
    now = time.time()
    eval_cd = _env_float(
        "ADA_AUTO_EVAL_COOLDOWN_SEC", 60.0, "JARVIS_AUTO_EVAL_COOLDOWN_SEC"
    )
    opt_cd = _env_float(
        "ADA_AUTO_OPT_COOLDOWN_SEC", 600.0, "JARVIS_AUTO_OPT_COOLDOWN_SEC"
    )

    do_eval = False
    do_opt = False
    with _lock:
        if _env_bool("ADA_AUTO_EVAL_GEN", False, "JARVIS_AUTO_EVAL_GEN") and (
            now - _last_eval_gen >= eval_cd
        ):
            do_eval = True
        if _env_bool(
            "ADA_AUTO_OPTIMIZATION_SUGGESTIONS",
            False,
            "JARVIS_AUTO_OPTIMIZATION_SUGGESTIONS",
        ) and (now - _last_opt >= opt_cd):
            do_opt = True

    if do_eval:
        try:
            from .eval_gen import generate_evals_from_logs

            generate_evals_from_logs(
                num_traces=_env_int(
                    "ADA_AUTO_EVAL_NUM_TRACES", 15, "JARVIS_AUTO_EVAL_NUM_TRACES"
                ),
                num_cases=_env_int(
                    "ADA_AUTO_EVAL_NUM_CASES", 2, "JARVIS_AUTO_EVAL_NUM_CASES"
                ),
                meta_source="eval_gen_auto",
            )
            with _lock:
                _last_eval_gen = time.time()
        except Exception as e:
            logger.warning("auto eval gen failed: %s", e)

    if do_opt:
        try:
            from .optimize import run_optimization_step

            run_optimization_step()
            with _lock:
                _last_opt = time.time()
        except Exception as e:
            logger.warning("auto optimization step failed: %s", e)
