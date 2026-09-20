"""Convert a user turn + supervisor seed into an explicit task specification."""
from __future__ import annotations

from typing import Any, Optional

from tools.shell_runner import is_shell_enabled

VALID_RISKS = ("read", "write", "high_impact")


def build_task_spec(
    message: str,
    decision: Optional[dict[str, Any]] = None,
    *,
    coding_mode: bool = False,
    workspace_root: Optional[str] = None,
) -> dict[str, Any]:
    """
    Software-owned success definition. Supervisor `agents[]` is a plan seed, not "done".
    """
    decision = decision or {}
    agents = decision.get("agents") or []
    goal = (
        (agents[0].get("goal") if agents else None)
        or decision.get("goal")
        or (message or "").strip()
        or "Respond to the user"
    )
    agent_names = [str(a.get("agent") or "") for a in agents if a.get("agent")]
    constraints: list[str] = []
    success: list[str] = []

    if coding_mode:
        constraints.append("Do not modify files outside the linked workspace")
        if workspace_root:
            constraints.append(f"Workspace root: {workspace_root}")
        success.append("Proposed edits stay under the workspace root")
    if "desktop" in agent_names:
        constraints.append("GUI control requires an armed desktop session")
        success.append("User-visible desktop goal completed or user was told why it stopped")
    if "shell" in agent_names:
        if not is_shell_enabled():
            constraints.append("Host shell is disabled")
        else:
            constraints.append("Shell writes require approval unless autonomy allows them")
        success.append("Commands ran only in the configured workdir")
    if "google" in agent_names:
        constraints.append("Do not send email or delete calendar events without approval when gated")
        success.append("Calendar/Gmail actions have an API acknowledgement")
    if "finance" in agent_names:
        constraints.append("Do not present market data as financial advice")
        success.append("Figures come from fetched tool data, not invented prices")
    if "coding" in agent_names:
        success.append("Tests passed, sandbox ran, or workspace proposals were produced")
        if workspace_root:
            success.append("Repo edits stayed in the overlay/worktree until apply")
    if not success:
        success.append("User request answered or a clear blocker is reported")

    risk = _infer_risk(agent_names, message)
    return {
        "goal": str(goal).strip(),
        "constraints": constraints,
        "success_criteria": success,
        "risk": risk,
        "budget": {
            "max_steps": 25,
            "max_specialists": 5,
        },
        "seed_agents": [{"agent": a.get("agent"), "goal": a.get("goal")} for a in agents],
    }


def _infer_risk(agent_names: list[str], message: str) -> str:
    low = (message or "").lower()
    high_ops = (
        "send email",
        "delete",
        "deploy",
        "rm -",
        "format ",
        "transfer",
        "wire ",
    )
    if "desktop" in agent_names or "shell" in agent_names:
        return "high_impact"
    if "google" in agent_names and any(k in low for k in ("send", "delete", "create event", "schedule")):
        return "high_impact"
    if any(k in low for k in high_ops):
        return "high_impact"
    if "coding" in agent_names or "google" in agent_names:
        return "write"
    return "read"


def format_task_spec_for_prompt(spec: dict[str, Any]) -> str:
    if not spec:
        return ""
    lines = [f"Goal: {spec.get('goal') or ''}"]
    risk = spec.get("risk")
    if risk:
        lines.append(f"Risk: {risk}")
    constraints = spec.get("constraints") or []
    if constraints:
        lines.append("Constraints:")
        lines.extend(f"- {c}" for c in constraints)
    criteria = spec.get("success_criteria") or []
    if criteria:
        lines.append("Success criteria:")
        lines.extend(f"- {c}" for c in criteria)
    return "\n".join(lines)
