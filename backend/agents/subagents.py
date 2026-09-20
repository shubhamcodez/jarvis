"""Subagents for context isolation: explore (read-only) and evaluate (critic)."""
from __future__ import annotations

from typing import Any, Optional

from agents.swe_loop import _llm_json, critic_evaluate
from agents.swe_tools import catalog_prompt, dispatch
from tools.overlay_workspace import OverlayWorkspace

_EXPLORE_SYSTEM = """You are Ada's read-only explore subagent.
Find the files and functions that matter. Do not edit.
Tools allowed: list_dir, grep, read_file, write_note, finish.
Output ONLY JSON: {"tool":"...","args":{}}
When you know the localization, finish with a summary of files, suspected cause, and test command.

""" + catalog_prompt()


def run_explore(
    goal: str,
    workspace: OverlayWorkspace,
    api_key: str,
    provider: str,
    *,
    run_id: str,
    max_steps: int = 8,
) -> dict[str, Any]:
    history = [{"role": "user", "content": f"Explore this repo for:\n{goal}\nFirst tool call now."}]
    plan: list[dict[str, Any]] = []
    summary = ""
    for _ in range(max_steps):
        raw = _llm_json(api_key, provider, _EXPLORE_SYSTEM, history, max_tokens=900)
        from agents.swe_loop import _parse_tool_call, _clip

        call = _parse_tool_call(raw)
        if not call:
            history.append({"role": "user", "content": 'Invalid JSON. Use {"tool":"...","args":{}}'})
            continue
        if call["tool"] in ("apply_patch", "write_file", "run_tests"):
            result = {"ok": False, "error": "explore is read-only"}
        else:
            result = dispatch(
                workspace,
                call["tool"],
                call["args"],
                run_id=run_id,
                plan=plan,
                allow_writes=False,
            )
        if call["tool"] == "finish":
            summary = str((call["args"] or {}).get("summary") or "")
            break
        history.append({"role": "assistant", "content": raw[:1500]})
        history.append({"role": "user", "content": _clip(result, 4000)})
    return {"ok": True, "summary": summary or "Explore finished without a summary."}


def run_evaluate(
    goal: str,
    changed: list[str],
    last_tests: Optional[dict[str, Any]],
    syntax: Optional[dict[str, Any]],
    summary: str,
    api_key: str,
    provider: str,
) -> dict[str, Any]:
    return critic_evaluate(api_key, provider, goal, changed, last_tests, syntax, summary)
