"""
SWE coding loop: localize → patch → test → critic.

This is the inner harness that SWE-bench / Aider-style tasks actually reward:
compact tools, overlay isolation, deterministic tests, and a separate critic.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client, should_omit_temperature
from tools.artifact_store import new_run_id, write_artifact
from tools.diagnostics import lsp_check_python, run_python_tests, syntax_check_python
from tools.localize import localize
from tools.overlay_workspace import OverlayWorkspace
from tools.project_rules import load_project_rules

from .swe_tools import catalog_prompt, dispatch

_MAX_STEPS = 18
_CRITIC_RETRIES = 2
_TOOL_RESULT_CHARS = 7000

_SWE_SYSTEM = """You are Jarvis's software engineering agent. This is a local unit-test coding exercise in a toy repository. You solve repo tasks with tools.

Stable workflow (do not skip):
1. Localize with list_dir, grep, read_file, and git_history (log/blame) until you know the exact files and lines.
2. update_plan with 3–8 concrete steps.
3. apply_patch with a unique old string. Prefer surgical edits over rewrite.
4. After each patch, fix LSP / syntax diagnostics before run_tests.
5. run_tests after edits. If tests fail, read the failure and patch again.
6. finish only when tests pass, syntax and LSP are clean, or you have a hard blocker.

Output ONLY a JSON object, no markdown:
{"tool": "<name>", "args": { ... }}

Tools:
"""

_CRITIC_SYSTEM = """You are a strict code critic. Do not write code. Judge whether the task is done.

Reply ONLY JSON:
{"score": 0.0-1.0, "passed": true|false, "issues": ["..."], "retry_instructions": "what to fix, or empty if passed"}

Rules:
- passed=true only if tests passed, or there are no tests AND syntax/LSP are clean AND the goal is visibly implemented.
- If tests failed or LSP reports errors, passed must be false.
- Be specific in retry_instructions (file + what is still wrong).
"""


def _parse_tool_call(raw: str) -> Optional[dict[str, Any]]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    if "tool" not in obj and "name" in obj:
        obj["tool"] = obj.get("name")
    if not obj.get("tool"):
        return None
    args = obj.get("args")
    if args is None:
        args = {k: v for k, v in obj.items() if k not in ("tool", "name")}
    if not isinstance(args, dict):
        args = {}
    return {"tool": str(obj.get("tool")), "args": args}


def _clip(obj: Any, limit: int = _TOOL_RESULT_CHARS) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except TypeError:
        text = str(obj)
    try:
        from observability.redact import redact_text

        text = redact_text(text, max_len=limit + 200)
    except Exception:
        pass
    if len(text) > limit:
        return text[: limit - 20] + "…[truncated]"
    return text


def _llm_json(
    api_key: str,
    provider: str,
    system: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1800,
    temperature: float = 0.1,
) -> str:
    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")
    create_kw: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        **chat_completion_limit_kwargs(provider, model, max_tokens),
    }
    if not should_omit_temperature(provider, model):
        create_kw["temperature"] = temperature
    resp = client.chat.completions.create(**create_kw)
    return (resp.choices[0].message.content or "").strip()


def critic_evaluate(
    api_key: str,
    provider: str,
    goal: str,
    changed: list[str],
    last_tests: Optional[dict[str, Any]],
    syntax: Optional[dict[str, Any]],
    summary: str,
    lsp: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    tests_ok = bool((last_tests or {}).get("passed")) if last_tests else None
    syn_ok = bool((syntax or {}).get("ok")) if syntax else None
    lsp_ok = bool((lsp or {}).get("ok")) if lsp else None
    if last_tests is not None and not tests_ok:
        return {
            "score": 0.2,
            "passed": False,
            "issues": ["automated tests failed"],
            "retry_instructions": (last_tests.get("summary") or "tests failed")[:800],
        }
    if syntax and not syn_ok:
        return {
            "score": 0.15,
            "passed": False,
            "issues": ["syntax errors"],
            "retry_instructions": json.dumps((syntax or {}).get("errors") or [])[:800],
        }
    if lsp and not lsp_ok:
        return {
            "score": 0.25,
            "passed": False,
            "issues": ["lsp diagnostics"],
            "retry_instructions": json.dumps((lsp or {}).get("diagnostics") or [])[:800],
        }
    payload = (
        f"Goal: {goal}\nChanged files: {changed}\n"
        f"Tests: {_clip(last_tests, 2500)}\nSyntax: {_clip(syntax, 800)}\n"
        f"LSP: {_clip(lsp, 800)}\n"
        f"Agent summary: {summary[:1500]}"
    )
    raw = _llm_json(
        api_key,
        provider,
        _CRITIC_SYSTEM,
        [{"role": "user", "content": payload}],
        max_tokens=400,
        temperature=0.0,
    )
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        out = json.loads(raw)
        return {
            "score": float(out.get("score") or 0),
            "passed": bool(out.get("passed")),
            "issues": list(out.get("issues") or []),
            "retry_instructions": str(out.get("retry_instructions") or ""),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {
            "score": 0.6 if tests_ok else 0.4,
            "passed": bool(tests_ok),
            "issues": [],
            "retry_instructions": "" if tests_ok else "Could not parse critic; re-run tests and fix remaining failures.",
        }


def run_swe_loop(
    goal: str,
    workspace_root: str,
    on_step: Optional[Callable] = None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    *,
    apply_writes: bool = False,
    project_context: str = "",
    max_steps: int = _MAX_STEPS,
    control_run_id: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    if api_key is None:
        from config import get_llm_api_key

        api_key = get_llm_api_key()

    goal = (goal or "").strip()
    if not goal:
        return "No task provided.", {}

    ws = OverlayWorkspace(workspace_root)
    run_id = new_run_id()
    plan: list[dict[str, Any]] = []
    loc = localize(ws, goal)
    thin = len(loc.get("files") or []) <= 1
    if thin and api_key:
        try:
            from agents.subagents import run_explore

            if on_step:
                on_step(0, "Localization was thin; running explore", "explore", "", None, False)
            exp = run_explore(goal, ws, api_key, provider, run_id=run_id, max_steps=5)
            extra = [p for p in (exp.get("files") or []) if p not in (loc.get("files") or [])]
            if extra:
                loc["files"] = list(loc.get("files") or []) + extra
            if exp.get("summary"):
                loc["explore"] = exp["summary"][:1500]
        except Exception:
            pass
    write_artifact("localize.json", json.dumps(loc, indent=2), run_id=run_id, kind="localize")
    rules = load_project_rules(workspace_root=str(ws.root))

    if on_step:
        on_step(
            0,
            "Localize → patch → test → critic",
            "plan",
            f"Likely files: {', '.join(loc.get('files') or []) or '(scan the tree)'}",
            None,
            False,
        )

    system = (
        _SWE_SYSTEM
        + catalog_prompt()
        + "\n\nWorkspace files (bounded):\n"
        + ws.tree_summary(160)
        + "\n\nCheap localization (ranked):\n"
        + json.dumps(
            {"files": loc.get("files"), "needles": loc.get("needles"), "explore": loc.get("explore")},
            ensure_ascii=False,
        )
    )
    if rules:
        system += "\n\n" + rules[:4000]
    try:
        from tools.skills import skills_for_prompt

        skill_block = skills_for_prompt(goal, str(ws.root))
        if skill_block:
            system += "\n\n" + skill_block[:3500]
    except Exception:
        pass
    try:
        from tools.github_issues import fetch_issue, format_issue_for_prompt

        issue = fetch_issue(goal)
        if issue.get("ok"):
            system += "\n\n" + format_issue_for_prompt(issue)[:4000]
            if on_step:
                on_step(0, f"Loaded GitHub issue: {issue.get('title')}", "github_issue", issue.get("url") or "", None, False)
    except Exception:
        pass
    if (project_context or "").strip():
        system += "\n\nSnapshot excerpt (may be stale; prefer tools):\n" + project_context[:4000]

    likely = ", ".join((loc.get("files") or [])[:6]) or "unknown"
    history: list[dict[str, str]] = [
        {
            "role": "user",
            "content": (
                f"Coding-benchmark task (not a real production system):\n{goal}\n\n"
                f"Start at these files: {likely}\n"
                "Read the failing tests, apply_patch the implementation, then run_tests. "
                "First tool call now."
            ),
        }
    ]
    last_tests: Optional[dict[str, Any]] = None
    last_syntax: Optional[dict[str, Any]] = None
    last_lsp: Optional[dict[str, Any]] = None
    last_summary = ""
    critic: Optional[dict[str, Any]] = None
    finished = False
    step_used = 0
    writes = 0
    readonly_streak = 0
    refusals = 0
    empty_json = 0
    stall = 0
    last_fp = ""
    lsp_blocked = False

    for step in range(1, max(2, int(max_steps)) + 1):
        if control_run_id:
            try:
                from agents.run_control import stop_reason

                halt = stop_reason(control_run_id)
                if halt:
                    return halt, {"name": "swe", "input": goal[:500], "result": "cancelled"}
            except Exception:
                pass
        step_used = step
        try:
            raw = _llm_json(api_key, provider, system, history, max_tokens=1800)
        except Exception as e:
            err = str(e)
            last_summary = f"Model error: {err}"
            if "403" in err or "permission-denied" in err.lower() or "can't help" in err.lower():
                refusals += 1
                if refusals >= 3:
                    break
                history = [
                    {
                        "role": "user",
                        "content": (
                            "Continue this programming exercise. Output only "
                            '{"tool":"read_file","args":{"path":"'
                            + ((loc.get("files") or ["pkg/__init__.py"])[0])
                            + '"}}'
                        ),
                    }
                ]
                system = _SWE_SYSTEM + catalog_prompt()
                continue
            break
        call = _parse_tool_call(raw)
        if not call:
            empty_json += 1
            if empty_json >= 3:
                last_summary = "Stopped: empty or invalid JSON"
                break
            history.append({"role": "assistant", "content": (raw or "")[:2000]})
            history.append({"role": "user", "content": 'Invalid JSON. Reply with only {"tool":"...","args":{}}'})
            continue
        empty_json = 0
        tool = call["tool"]
        args = call["args"]
        if lsp_blocked and tool not in ("read_file", "apply_patch", "write_file", "run_tests", "update_plan"):
            result = {
                "ok": False,
                "error": "LSP errors remain; use read_file, apply_patch, write_file, or run_tests until clean",
            }
        else:
            result = dispatch(ws, tool, args, run_id=run_id, plan=plan, allow_writes=True)
        if tool in ("apply_patch", "write_file") and result.get("ok"):
            writes += 1
            readonly_streak = 0
        elif tool in ("list_dir", "grep", "read_file"):
            readonly_streak += 1
        if tool == "run_tests":
            last_tests = (result.get("tests") or result) if isinstance(result, dict) else None
            last_syntax = result.get("syntax") if isinstance(result, dict) else None
            last_lsp = result.get("lsp") if isinstance(result, dict) else last_lsp
        if tool in ("apply_patch", "write_file") and result.get("ok"):
            last_syntax = syntax_check_python(ws)
            last_lsp = lsp_check_python(ws)
            result = {**result, "syntax": last_syntax, "lsp": last_lsp}
        if last_lsp is not None:
            lsp_blocked = not bool(last_lsp.get("ok"))
        tsum = ""
        if isinstance(result, dict):
            tests = result.get("tests") if isinstance(result.get("tests"), dict) else {}
            tsum = str((tests or {}).get("summary") or result.get("error") or "")[:80]
        fp = f"{tool}|{args.get('path') or ''}|{tsum}"
        if fp == last_fp:
            stall += 1
        else:
            stall = 0
            last_fp = fp
        if on_step:
            desc = tool
            if tool == "apply_patch":
                desc = f"patch {args.get('path')}"
            elif tool == "read_file":
                desc = f"read {args.get('path')}"
            elif tool == "grep":
                desc = f"grep {args.get('pattern')}"
            on_step(step, raw[:400], tool, desc, _clip(result, 800), False)

        if tool == "finish":
            if writes and last_tests is None:
                history.append({"role": "assistant", "content": raw[:2500]})
                history.append(
                    {
                        "role": "user",
                        "content": "finish refused: call run_tests after writes, then finish.",
                    }
                )
                continue
            last_summary = str((args or {}).get("summary") or result.get("summary") or "")
            if last_tests is None and ws.changed_paths():
                last_syntax = syntax_check_python(ws)
                last_lsp = lsp_check_python(ws)
                last_tests = run_python_tests(ws)
            critic = critic_evaluate(
                api_key,
                provider,
                goal,
                ws.changed_paths(),
                last_tests,
                last_syntax,
                last_summary,
                last_lsp,
            )
            if critic.get("passed") or critic.get("score", 0) >= 0.85:
                finished = True
                break
            retries_left = _CRITIC_RETRIES - (step // 6)
            if retries_left <= 0:
                finished = True
                break
            history.append({"role": "assistant", "content": raw[:2500]})
            history.append(
                {
                    "role": "user",
                    "content": (
                        "Critic rejected the finish.\n"
                        + _clip(critic, 1500)
                        + "\nContinue with tools. Do not finish until issues are fixed."
                    ),
                }
            )
            continue

        history.append({"role": "assistant", "content": raw[:2500]})
        follow = f"Tool result for {tool}:\n{_clip(result)}"
        if readonly_streak >= 3 and writes == 0:
            follow += (
                "\n\nYou have only inspected files. apply_patch the implementation now. "
                "Do not finish until a patch is applied and run_tests has been called."
            )
        if tool == "apply_patch" and not result.get("ok"):
            follow += "\nPatch failed. Re-read the file (no line-number prefixes in old) and try a smaller unique old string."
        if stall >= 2:
            follow += "\n\nNo progress: you repeated the same tool/path. Try a different file or a smaller unique patch."
        history.append({"role": "user", "content": follow})
        if stall >= 4:
            last_summary = "Stopped: no progress"
            break
        if len(history) > 20:
            compact = "Earlier steps compacted. Plan=" + json.dumps(plan) + " Changed=" + ",".join(ws.changed_paths())
            history = [{"role": "user", "content": compact}, *history[-12:]]

    if not finished and last_tests is None and ws.changed_paths():
        last_syntax = syntax_check_python(ws)
        last_lsp = lsp_check_python(ws)
        last_tests = run_python_tests(ws)
        critic = critic_evaluate(
            api_key, provider, goal, ws.changed_paths(), last_tests, last_syntax, last_summary, last_lsp
        )

    checkpoint_id = ""
    if apply_writes and ws.changed_paths():
        try:
            from agents.run_control import current_run_id, record_checkpoint

            snap = record_checkpoint(
                current_run_id(),
                kind="overlay_turn",
                summary=f"overlay apply {len(ws.changed_paths())} files",
                payload=ws.originals_for_changed(),
            )
            if snap:
                checkpoint_id = str(snap.get("id") or "")
        except Exception:
            pass
        ws.apply_to_root()

    fences = ws.ada_file_fences()
    tests_line = ""
    if last_tests is not None:
        tests_line = "\n\n**Tests:** " + ("passed" if last_tests.get("passed") else "failed")
        if last_tests.get("summary"):
            tests_line += f"\n```\n{last_tests.get('summary')[:1800]}\n```"
    critic_line = ""
    if critic:
        critic_line = f"\n\n**Critic:** score={critic.get('score')} passed={critic.get('passed')}"
        if critic.get("issues"):
            critic_line += " — " + "; ".join(str(x) for x in critic["issues"][:4])

    intro = (
        f"**SWE task:** {goal}\n\n"
        f"Changed files: {', '.join(ws.changed_paths()) or '(none)'}\n"
        f"Steps: {step_used}. Isolation: overlay worktree"
        + (" applied to disk." if apply_writes else " — review diffs before apply.")
        + (f"\nCheckpoint `{checkpoint_id}` — `/undo` restores this turn." if checkpoint_id else "")
        + tests_line
        + critic_line
        + (f"\n\n{last_summary}" if last_summary else "")
    )
    reply = intro
    if fences and not apply_writes:
        reply = intro + "\n\n" + fences
    elif fences and apply_writes:
        reply = intro + "\n\n_Edits written to the workspace._"

    if on_step:
        on_step(
            step_used + 1,
            last_summary or "SWE loop complete.",
            "done",
            reply[:500],
            None,
            True,
        )

    tool_used = {
        "name": "swe_loop",
        "input": goal[:4000],
        "result": json.dumps(
            {
                "changed": ws.changed_paths(),
                "tests": {"passed": (last_tests or {}).get("passed")} if last_tests else None,
                "critic": critic,
                "run_id": run_id,
                "apply_writes": apply_writes,
            },
            ensure_ascii=False,
        )[:8000],
    }
    return reply, tool_used
