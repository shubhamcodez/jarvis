"""Persistent AgentState — source of truth outside the model context window."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import chats_dir


def _state_dir() -> Path:
    d = Path(chats_dir()) / "agent-state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for(chat_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (chat_id or "default"))
    return _state_dir() / f"{safe}.json"


def empty_state(chat_id: str = "", run_id: Optional[str] = None) -> dict[str, Any]:
    return {
        "chat_id": chat_id or "",
        "run_id": run_id or str(uuid.uuid4()),
        "goal": "",
        "task_spec": {},
        "plan": [],
        "current_step": 0,
        "findings": [],
        "tool_results": [],
        "artifacts": [],
        "errors": [],
        "unresolved": [],
        "budget": {"max_steps": 20, "max_specialists": 5, "steps_used": 0, "replans": 0},
        "hitl": [],
        "action_signatures": [],
        "task_id": "",
        "status": "idle",
        "next_action": "",
        "owner": "ada",
        "updated_at": time.time(),
    }


def load_state(chat_id: Optional[str]) -> dict[str, Any]:
    if not chat_id:
        return empty_state()
    path = _path_for(chat_id)
    if not path.exists():
        return empty_state(chat_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = empty_state(chat_id)
            base.update(data)
            return base
    except (OSError, json.JSONDecodeError):
        pass
    return empty_state(chat_id)


def save_state(state: dict[str, Any]) -> None:
    chat_id = (state.get("chat_id") or "").strip()
    if not chat_id:
        return
    state["updated_at"] = time.time()
    path = _path_for(chat_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def begin_run(
    chat_id: Optional[str],
    task_spec: dict[str, Any],
    seed_agents: Optional[list[dict[str, Any]]] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    st = load_state(chat_id)
    st["chat_id"] = chat_id or st.get("chat_id") or ""
    st["run_id"] = run_id or str(uuid.uuid4())
    st["goal"] = (task_spec or {}).get("goal") or ""
    st["task_spec"] = task_spec or {}
    budget = dict(st.get("budget") or {})
    spec_budget = (task_spec or {}).get("budget") or {}
    budget.update({k: spec_budget[k] for k in spec_budget})
    budget["steps_used"] = 0
    budget["replans"] = 0
    st["budget"] = budget
    st["errors"] = []
    st["action_signatures"] = []
    st["findings"] = []
    st["tool_results"] = []
    st["artifacts"] = []
    st["unresolved"] = []
    st["hitl"] = []
    plan = []
    for i, item in enumerate(seed_agents or task_spec.get("seed_agents") or []):
        plan.append(
            {
                "id": i + 1,
                "goal": item.get("goal") or "",
                "status": "pending",
                "depends_on": [i] if i else [],
                "agent": item.get("agent"),
            }
        )
    if plan:
        plan[0]["status"] = "active"
    st["plan"] = plan
    st["current_step"] = 1 if plan else 0
    st["status"] = "active"
    st["owner"] = (plan[0].get("agent") if plan else None) or "ada"
    st["next_action"] = (plan[0].get("goal") if plan else "") or st.get("goal") or ""
    save_state(st)
    if plan:
        try:
            from agents.tasks import create_task

            task = create_task(
                chat_id=st.get("chat_id") or "",
                run_id=st.get("run_id") or "",
                title=st.get("goal") or "",
                goal=st.get("goal") or "",
                owner=st.get("owner") or "ada",
                plan=plan,
                next_action=st.get("next_action") or "",
            )
            st["task_id"] = task.get("id") or ""
            save_state(st)
        except Exception:
            pass
    return st


def resume_run(chat_id: Optional[str], task: dict[str, Any]) -> dict[str, Any]:
    st = load_state(chat_id)
    st["chat_id"] = chat_id or st.get("chat_id") or task.get("chat_id") or ""
    st["run_id"] = task.get("run_id") or st.get("run_id") or str(uuid.uuid4())
    st["goal"] = task.get("goal") or st.get("goal") or ""
    st["plan"] = list(task.get("plan") or st.get("plan") or [])
    st["task_id"] = task.get("id") or ""
    st["status"] = "active"
    st["owner"] = task.get("owner") or "ada"
    st["next_action"] = task.get("next_action") or ""
    for step in st["plan"]:
        if step.get("status") == "error":
            step["status"] = "pending"
    if st["plan"] and not any(s.get("status") == "active" for s in st["plan"]):
        for step in st["plan"]:
            if step.get("status") == "pending":
                step["status"] = "active"
                break
    save_state(st)
    try:
        from agents.tasks import update_task

        update_task(st["task_id"], status="active", run_id=st["run_id"], plan=st["plan"])
    except Exception:
        pass
    return st


def mark_plan_step(state: dict[str, Any], index: int, status: str, finding: Optional[str] = None) -> dict[str, Any]:
    plan = list(state.get("plan") or [])
    if 0 <= index < len(plan):
        plan[index]["status"] = status
        if index + 1 < len(plan) and status == "complete":
            if plan[index + 1].get("status") == "pending":
                plan[index + 1]["status"] = "active"
                state["current_step"] = plan[index + 1].get("id") or (index + 2)
    state["plan"] = plan
    if finding:
        findings = list(state.get("findings") or [])
        findings.append(finding[:2000])
        state["findings"] = findings[-40:]
    active = next((s for s in plan if s.get("status") == "active"), None)
    if active:
        state["next_action"] = f"{active.get('agent') or ''}: {active.get('goal') or ''}".strip()
        state["owner"] = active.get("agent") or state.get("owner") or "ada"
    save_state(state)
    try:
        from agents.tasks import sync_from_agent_state

        sync_from_agent_state(state)
    except Exception:
        pass
    return state


def record_action_signature(state: dict[str, Any], signature: str) -> dict[str, Any]:
    sigs = list(state.get("action_signatures") or [])
    sigs.append((signature or "")[:300])
    state["action_signatures"] = sigs[-80:]
    budget = dict(state.get("budget") or {})
    budget["steps_used"] = int(budget.get("steps_used") or 0) + 1
    state["budget"] = budget
    save_state(state)
    return state


def record_error(state: dict[str, Any], error: str) -> dict[str, Any]:
    errors = list(state.get("errors") or [])
    errors.append((error or "")[:1500])
    state["errors"] = errors[-20:]
    save_state(state)
    return state


def append_hitl(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    hitl = list(state.get("hitl") or [])
    hitl.append(item)
    state["hitl"] = hitl[-30:]
    save_state(state)
    return state


def structured_view(state: dict[str, Any]) -> str:
    """Compact structured state for the context builder (not raw traces)."""
    if not state:
        return ""
    lines = []
    if state.get("goal"):
        lines.append(f"Goal: {state['goal']}")
    plan = state.get("plan") or []
    if plan:
        lines.append("Plan:")
        for step in plan:
            lines.append(
                f"  [{step.get('status')}] #{step.get('id')} {step.get('agent')}: {step.get('goal')}"
            )
    if state.get("next_action"):
        lines.append(f"Next action: {state['next_action']}")
    if state.get("task_id"):
        lines.append(f"Task: {state['task_id']} ({state.get('status') or 'active'})")
    findings = state.get("findings") or []
    if findings:
        lines.append("Findings: " + "; ".join(findings[-5:]))
    unresolved = state.get("unresolved") or []
    if unresolved:
        lines.append("Unresolved: " + "; ".join(unresolved[-4:]))
    errors = state.get("errors") or []
    if errors:
        lines.append("Recent errors: " + "; ".join(errors[-3:]))
    return "\n".join(lines)
