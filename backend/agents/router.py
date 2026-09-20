"""Router graph: supervisor builds an agent plan → chat | run_agent_plan (one or many specialists)."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Literal, Optional

from langgraph.graph import END, StateGraph

from .state import RouterState
from tools.runner import run_tools_for_turn

_STEP_BUCKET = 80


async def _supervisor_node(state: RouterState) -> RouterState:
    from .supervisor import compute_supervisor_decision

    message = (state.get("message") or "").strip()
    api_key = state["api_key"]
    provider = state.get("provider") or "openai"
    existing = state.get("supervisor_decision")
    recent_turns = []
    chat_id = state.get("chat_id") or ""
    if chat_id:
        try:
            from memory.chat_log import read_chat_log

            recent_turns = read_chat_log(chat_id)[-8:]
        except Exception:
            recent_turns = []
    thread = {}
    try:
        from agents.agent_state import load_state
        from memory.thread_context import infer_thread_from_turns, merge_thread, thread_from_state

        thread = merge_thread(thread_from_state(load_state(chat_id)), infer_thread_from_turns(recent_turns))
    except Exception:
        thread = {}
    from observability.spans import span

    with span("supervisor", provider=provider):
        if isinstance(existing, dict) and (existing.get("agents") or existing.get("agent") or existing.get("run_agent") is False):
            decision = existing
        else:
            decision = await asyncio.to_thread(
                compute_supervisor_decision,
                api_key,
                provider,
                message,
                coding_mode=bool(state.get("coding_mode")),
                coding_project_context=(state.get("coding_project_context") or ""),
                recent_turns=recent_turns,
            )
    agents = decision.get("agents") or []
    tools = state.get("custom_agent_tools")
    if tools is not None:
        from custom_agents.context import filter_supervisor_plan

        agents = filter_supervisor_plan(agents, list(tools))
        decision = {**decision, "agents": agents, "run_agent": bool(agents)}
        if agents:
            decision["agent"] = agents[0].get("agent")
            decision["goal"] = agents[0].get("goal") or decision.get("goal")
        else:
            decision["agent"] = None
            decision["goal"] = None
    goal = (
        (agents[0].get("goal") if agents else None)
        or (decision.get("goal") or "")
        or message
    ).strip()
    from config import get_workspace_root
    from .agent_state import begin_run, load_state
    from .task_spec import build_task_spec

    from agents.execution_policy import mode_label

    if mode_label() == "plan":
        decision = {**decision, "run_agent": False, "agents": []}
        agents = []
        goal = (decision.get("goal") or message or "").strip()
    spec = build_task_spec(
        message,
        decision,
        coding_mode=bool(state.get("coding_mode")),
        workspace_root=get_workspace_root() or None,
    )
    resume_id = (state.get("resume_task_id") or "").strip()
    if resume_id:
        from agents.tasks import get_task
        from .agent_state import resume_run

        task = get_task(resume_id)
        if task:
            ast = resume_run(
                state.get("chat_id") or task.get("chat_id"),
                task,
                preferred_run_id=state.get("run_id"),
            )
            from agents.tasks import remaining_plan

            leftover = remaining_plan(task)
            if leftover:
                decision = {**decision, "run_agent": True, "agents": leftover}
                agents = leftover
            else:
                decision = {**decision, "run_agent": False, "agents": []}
                agents = []
        else:
            ast = begin_run(state.get("chat_id"), spec, agents, run_id=state.get("run_id"))
    elif agents or decision.get("run_agent"):
        ast = begin_run(state.get("chat_id"), spec, agents, run_id=state.get("run_id"))
    else:
        ast = load_state(state.get("chat_id"))
    if agents or decision.get("run_agent"):
        try:
            from agents.run_control import get_run, start_run

            rid = ast.get("run_id")
            rec = get_run(rid) if rid else None
            if rec and rec.get("status") == "cancelled":
                pass
            else:
                start_run(
                    chat_id=state.get("chat_id") or "",
                    task_id=ast.get("task_id") or "",
                    run_id=rid,
                )
        except Exception:
            logging.getLogger("jarvis.router").warning("start_run attach failed", exc_info=True)
    return {
        "supervisor_decision": decision,
        "goal": goal,
        "task_spec": spec,
        "agent_state": ast,
        "run_id": ast.get("run_id"),
        "task_id": ast.get("task_id"),
    }


async def _chat_node(state: RouterState) -> RouterState:
    from agents.models import get_llm_client
    from config import get_chat_history_limit, get_llm_api_key, get_openai_api_key
    from memory import get_memory_store, run_retrieval_pipeline
    from memory.chat_log import read_chat_log

    api_key = state["api_key"]
    provider = state.get("provider") or "openai"
    client = get_llm_client(provider)
    message = (state.get("message") or "").strip()
    paths = state.get("attachment_paths") or []
    if not message and paths:
        message = "Please summarize or answer based on the attached documents."

    chat_id = state.get("chat_id") or ""
    # Load conversation history (frontend already appended current user message)
    recent_turns = read_chat_log(chat_id) if chat_id else []
    max_history = get_chat_history_limit()
    history = recent_turns[-max_history:] if recent_turns else None

    from memory.prompt_assembly import assemble_turn_context
    from tools.project_rules import load_project_rules
    from .agent_state import structured_view
    from .task_spec import format_task_spec_for_prompt
    from observability.spans import span

    memory_context = ""
    with span("retrieval", chat_id=chat_id, route="chat") as sp:
        try:
            store = get_memory_store()
            if len(store) > 0:
                try:
                    openai_api_key = get_openai_api_key()
                except ValueError:
                    openai_api_key = get_llm_api_key()
                task_state = {"goal": state.get("goal"), "route": "chat"}
                memory_context, hits = run_retrieval_pipeline(
                    store,
                    openai_api_key,
                    current_message=message,
                    recent_turns=recent_turns,
                    task_state=task_state,
                    top_k=8,
                    include_raw_top_n=3,
                    max_memory_raw_chars=1800,
                )
                sp.set(hits=len(hits), store_size=len(store))
                try:
                    from observability.metrics import incr, observe

                    incr("retrieval.calls")
                    observe("retrieval.hits", float(len(hits)))
                except Exception:
                    pass
        except Exception as exc:
            sp.fail(str(exc))

    wq = (state.get("web_search_query") or "").strip() or None
    allowed = state.get("custom_agent_tools")
    with span("tools", chat_id=chat_id):
        tool_system, tool_used = run_tools_for_turn(
            message or "",
            recent_turns=recent_turns or [],
            web_search_query=wq,
            allowed_tools=set(allowed) if allowed is not None else None,
        )

    pack = assemble_turn_context(
        user_message=message,
        history=history,
        memory_context=memory_context or "",
        tool_system=tool_system or "",
        task_spec_text=format_task_spec_for_prompt(state.get("task_spec") or {}),
        agent_state_text=structured_view(state.get("agent_state") or {}),
        custom_agent_system=(state.get("custom_agent_system") or "").strip(),
        project_rules=load_project_rules() or "",
        untrusted_tools=bool(wq),
    )
    system_content = pack.system or None
    with span("llm", provider=provider, route="chat") as sp:
        reply = await asyncio.to_thread(
            client.chat,
            api_key,
            pack.user_message or message or "Hello.",
            paths if paths else None,
            pack.history or None,
            system_content,
        )
        sp.set(**{k: pack.stats.get(k) for k in ("system_tokens", "stable_tokens", "dynamic_tokens")})
    out: RouterState = {"reply": reply, "route": "chat"}
    if tool_used:
        out["tool_used"] = tool_used
    return out


def _emit_supervisor_step(state: RouterState) -> None:
    """Emit supervisor's next_steps as step 0 so the UI shows the plan."""
    on_step = state.get("on_step")
    if not on_step:
        return
    decision = state.get("supervisor_decision") or {}
    reasoning = decision.get("reasoning") or ""
    next_steps = decision.get("next_steps") or "Running agent."
    agents = decision.get("agents") or []
    if len(agents) > 1:
        chain = " → ".join((x.get("agent") or "?") for x in agents)
        next_steps = f"{next_steps}\n\nAgents: {chain}"
    on_step(0, reasoning, "supervisor", next_steps, None, False)


def _wrap_on_step_for_plan(
    on_step: Optional[Callable],
    bucket_index: int,
    agent_key: str,
) -> Optional[Callable]:
    if not on_step:
        return None
    label = {
        "desktop": "Desktop",
        "coding": "Coding",
        "shell": "Shell",
        "finance": "Finance",
        "google": "Google",
    }.get(agent_key, agent_key)
    off = bucket_index * _STEP_BUCKET

    def wrapped(step, thought, action, description, result, done, screenshot_base64=None):
        desc = (description or "").strip()
        prefixed = f"[{label}] {desc}" if desc else f"[{label}]"
        on_step(step + off, thought, action, prefixed, result, done, screenshot_base64)

    return wrapped


async def _run_agent_plan_node(state: RouterState) -> RouterState:
    """Run one or more specialist agents in order; later steps see truncated prior outputs."""
    from .coding_agent import run_coding_agent
    from .desktop_agent import run_desktop_agent
    from .finance_agent import run_finance_agent
    from .google_workspace_agent import run_google_workspace_agent
    from .shell_agent import run_shell_agent
    from tools.web_search import augment_goal_with_web_search

    decision = state.get("supervisor_decision") or {}
    plan = decision.get("agents") or []
    if not plan:
        return {"reply": "No agent plan to run.", "route": "chat"}

    _emit_supervisor_step(state)
    on_step = state.get("on_step")
    api_key = state["api_key"]
    provider = state.get("provider") or "openai"

    route_by_agent = {
        "desktop": "run_desktop",
        "coding": "run_coding",
        "shell": "run_shell",
        "finance": "run_finance",
        "google": "run_google",
    }

    sections: list[str] = []
    prev_snippets: list[str] = []
    last_tool: Optional[dict] = None
    last_route = "run_multi_agent"

    run_id = state.get("run_id") or (state.get("agent_state") or {}).get("run_id")
    for idx, item in enumerate(plan):
        from agents.run_control import (
            finish_child,
            is_cancelled,
            pop_steer,
            register_child,
            spend_ok,
        )

        if is_cancelled(run_id):
            sections.append("_Stopped by user._")
            break
        ok_spend, spend_info = spend_ok(run_id)
        if not ok_spend:
            sections.append(
                f"_Spend cap reached ({spend_info.get('tokens_used')} / {spend_info.get('max_tokens')} tokens)._"
            )
            break
        agent = item.get("agent")
        base_goal = (item.get("goal") or "").strip()
        if not agent or not base_goal:
            continue
        steer = pop_steer(run_id)
        if steer:
            base_goal = f"{base_goal}\n\nUser steer (follow this): {steer}"
        child_id = register_child(run_id, name=str(agent), agent=str(agent))

        if idx > 0 and prev_snippets:
            ctx = (
                "\n\n---\n**Earlier agents in this run (context; use facts below):**\n\n"
                + "\n\n".join(prev_snippets[-3:])
            )
            full_goal = base_goal + ctx
        else:
            full_goal = base_goal

        if idx == 0:
            goal_run, ws_tool = augment_goal_with_web_search(dict(state) | {"goal": full_goal})
            if ws_tool:
                last_tool = ws_tool
        else:
            goal_run = full_goal
            ws_tool = None

        wrapped = _wrap_on_step_for_plan(on_step, idx, str(agent))

        from .agent_state import mark_plan_step, record_action_signature, record_error
        from observability.spans import span

        ast = state.get("agent_state") or {}
        try:
            with span("specialist", agent=str(agent), index=idx, run_id=run_id or ""):
                if agent == "desktop":
                    from agents.computer_use.budget import parse_duration, steps_for_budget

                    dur = parse_duration(goal_run)
                    steps = steps_for_budget(dur, 25)
                    reply = await asyncio.to_thread(
                        run_desktop_agent,
                        goal_run,
                        steps,
                        wrapped,
                        api_key=api_key,
                        provider=provider,
                        duration_sec=dur,
                        run_id=run_id,
                    )
                    tu = None
                elif agent == "coding":
                    reply, tu = await asyncio.to_thread(
                        run_coding_agent,
                        goal_run,
                        wrapped,
                        api_key,
                        provider,
                        project_context=state.get("coding_project_context") or None,
                        run_id=run_id,
                    )
                elif agent == "shell":
                    reply, tu = await asyncio.to_thread(
                        run_shell_agent,
                        goal_run,
                        wrapped,
                        api_key,
                        provider,
                        chat_id=state.get("chat_id"),
                        run_id=run_id,
                    )
                elif agent == "finance":
                    reply, tu = await asyncio.to_thread(
                        run_finance_agent,
                        goal_run,
                        wrapped,
                        api_key,
                        provider,
                        run_id,
                    )
                elif agent == "google":
                    reply, tu = await asyncio.to_thread(
                        run_google_workspace_agent,
                        goal_run,
                        state.get("google_session_id"),
                        wrapped,
                        api_key,
                        provider,
                        state.get("chat_id"),
                        run_id,
                    )
                else:
                    finish_child(run_id, child_id, "skipped")
                    ast = record_error(ast, f"unknown agent: {agent}")
                    continue
        except Exception as e:
            reply = f"**{agent}** failed: {e}"
            tu = None
            ast = record_error(ast, str(e))
        reply_l = (reply or "").lower()
        pending = bool(isinstance(tu, dict) and tu.get("pending_approval")) or "awaiting user confirmation" in reply_l
        tool_ok = True
        if isinstance(tu, dict):
            result = tu.get("result")
            if isinstance(result, dict) and result.get("ok") is False:
                tool_ok = False
            elif isinstance(result, str) and ("failed" in result.lower() or '"ok": false' in result.lower()):
                tool_ok = False
        failed = (not tool_ok) or ("is not armed" in reply_l) or reply_l.startswith(
            f"**{agent}** failed:"
        )
        if pending:
            finish_child(run_id, child_id, "blocked")
        else:
            finish_child(run_id, child_id, "error" if failed else "complete")

        ast = record_action_signature(ast, f"{agent}|{idx}|{base_goal[:80]}")
        plan_index = item.get("index")
        if not isinstance(plan_index, int):
            plan_index = idx
        ast = mark_plan_step(
            ast,
            plan_index,
            "error" if failed else ("blocked" if pending else "complete"),
            finding=(reply or "")[:400],
        )

        if tu:
            last_tool = tu
        elif ws_tool and last_tool is None:
            last_tool = ws_tool

        title = {
            "desktop": "Desktop",
            "coding": "Coding",
            "shell": "Shell",
            "finance": "Finance",
            "google": "Google",
        }.get(agent, agent)
        sections.append(f"### {title}\n\n{reply}")
        prev_snippets.append(f"**{title}:**\n{(reply or '')[:8000]}")
        last_route = route_by_agent.get(str(agent), last_route)

    if not sections:
        return {"reply": "No agent steps completed.", "route": "chat"}

    combined = "\n\n".join(sections)
    ast = state.get("agent_state") or {}
    from .control_loop import classify_failure, should_replan, should_stop, verify_success_criteria
    from agents.hitl import list_pending

    verify = verify_success_criteria(ast, combined)
    stop, stop_reason = should_stop(ast)
    replan, replan_why = should_replan(ast)
    failure_class = None
    if not verify.get("ok") or stop:
        failure_class = classify_failure(
            (ast.get("errors") or [None])[-1] if ast.get("errors") else None,
            verify,
            stop_reason or replan_why,
        )
    if replan and not stop:
        budget = dict(ast.get("budget") or {})
        budget["replans"] = int(budget.get("replans") or 0) + 1
        ast["budget"] = budget
        from .agent_state import save_state

        save_state(ast)
        combined += (
            f"\n\n_Control policy: stalled ({replan_why}). "
            "No automatic replan was run — retry with a narrower goal if needed._"
        )

    out: RouterState = {
        "reply": combined,
        "route": "run_multi_agent" if len(sections) > 1 else last_route,
        "agent_state": ast,
        "run_id": ast.get("run_id") or state.get("run_id"),
        "failure_class": failure_class,
        "pending_approvals": list_pending(state.get("chat_id")),
    }
    if last_tool:
        out["tool_used"] = last_tool
    return out


def _route_after_start(state: RouterState) -> Literal["chat", "supervisor"]:
    message = (state.get("message") or "").strip()
    paths = state.get("attachment_paths") or []
    if not message and paths:
        return "chat"
    return "supervisor"


def _route_after_supervisor(state: RouterState) -> Literal["chat", "run_agent_plan"]:
    decision = state.get("supervisor_decision") or {}
    if not decision.get("run_agent"):
        return "chat"
    agents = decision.get("agents") or []
    if not agents:
        return "chat"
    return "run_agent_plan"


def create_router_graph():
    """Build the graph: start → [chat | supervisor] → [chat | run_agent_plan] → END."""
    builder = StateGraph(RouterState)

    builder.add_node("supervisor", _supervisor_node)
    builder.add_node("chat", _chat_node)
    builder.add_node("run_agent_plan", _run_agent_plan_node)

    builder.add_conditional_edges(
        "__start__",
        _route_after_start,
        path_map={"chat": "chat", "supervisor": "supervisor"},
    )
    builder.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        path_map={"chat": "chat", "run_agent_plan": "run_agent_plan"},
    )
    builder.add_edge("chat", END)
    builder.add_edge("run_agent_plan", END)

    return builder.compile()
