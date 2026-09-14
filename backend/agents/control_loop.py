"""Progress detection, replan triggers, and deterministic verification helpers."""
from __future__ import annotations

from typing import Any, Optional

from observability.guards import should_force_replan, should_stop_streak


def plan_unchanged(state: dict[str, Any]) -> bool:
    plan = state.get("plan") or []
    if not plan:
        return False
    return all((s.get("status") or "pending") in ("pending", "active") for s in plan) and len(
        state.get("action_signatures") or []
    ) >= 4


def should_replan(state: dict[str, Any], last_error: Optional[str] = None) -> tuple[bool, str]:
    if last_error:
        return True, "tool_or_agent_error"
    if should_force_replan(state.get("action_signatures") or []):
        return True, "repeated_action_signatures"
    if plan_unchanged(state):
        return True, "plan_stalled"
    budget = state.get("budget") or {}
    used = int(budget.get("steps_used") or 0)
    max_steps = int(budget.get("max_steps") or 20)
    if used >= max_steps:
        return False, "budget_exhausted"
    return False, ""


def should_stop(state: dict[str, Any]) -> tuple[bool, str]:
    budget = state.get("budget") or {}
    used = int(budget.get("steps_used") or 0)
    max_steps = int(budget.get("max_steps") or 20)
    if used >= max_steps:
        return True, "budget_exhausted"
    sigs = state.get("action_signatures") or []
    if len(sigs) >= 3 and should_stop_streak(sigs[-1], "", [{"action": s} for s in sigs], streak_limit=3):
        return True, "no_progress"
    replans = int(budget.get("replans") or 0)
    if replans >= 2:
        return True, "replan_limit"
    return False, ""


def verify_success_criteria(state: dict[str, Any], reply: str) -> dict[str, Any]:
    """
    Deterministic checks only. Does not ask the same model if it 'succeeded'.
    """
    spec = state.get("task_spec") or {}
    criteria = list(spec.get("success_criteria") or [])
    errors = state.get("errors") or []
    results: list[dict[str, Any]] = []
    all_ok = True
    text = (reply or "").lower()
    for c in criteria:
        cl = c.lower()
        ok = True
        reason = "stated in reply or no hard failure"
        if "api acknowledgement" in cl:
            ok = "ok: false" not in text and "awaiting user confirmation" not in text
            reason = "API ack present" if ok else "missing API ack or pending approval"
        if "sandbox" in cl and errors:
            ok = False
            reason = "sandbox/agent errors recorded"
        if not ok:
            all_ok = False
        results.append({"criterion": c, "ok": ok, "reason": reason})
    if errors and all_ok:
        all_ok = False
        results.append({"criterion": "no_unresolved_errors", "ok": False, "reason": errors[-1]})
    return {"ok": all_ok, "checks": results}


def classify_failure(first_error: Optional[str], verify: Optional[dict[str, Any]], stop_reason: str) -> str:
    if first_error:
        low = first_error.lower()
        if "approval" in low or "armed" in low:
            return "permission"
        if "token" in low or "oauth" in low:
            return "permission"
        if "argument" in low or "requires" in low:
            return "tool_arguments"
        return "tool_execution"
    if verify and not verify.get("ok"):
        return "verification"
    if stop_reason in ("budget_exhausted", "no_progress", "replan_limit"):
        return "termination"
    return "reasoning"
