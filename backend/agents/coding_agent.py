"""Coding agent: LLM writes Python for the goal, runs it in the sandbox, returns output (no GUI)."""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client, should_omit_temperature
from tools.python_sandbox import extract_python_fences, run_sandboxed_python
from tools.sandbox_markdown import redact_sandbox_result_dict, redact_image_stdout, stdout_to_markdown_body

_MAX_PROJECT_CONTEXT_CHARS = 20_000
_MIN_CTX_FOR_WORKSPACE_PROPOSE = 80

_CODING_GEN_SYSTEM = """You are Jarvis's coding agent. The user gave a task that should be solved with Python code running in a secure sandbox—not by clicking the desktop.

**Role vs finance agent:** The finance agent fetches market **data** and short factual/market commentary. **You** run **code**: statistics, transforms, simulations, and **plots**. If they want charts, regressions, correlations, backtests, or custom analysis on prices/returns, do it here with numpy/pandas/matplotlib/yfinance as needed.

If a **project repository snapshot** is included, use it to answer questions about that codebase or to mirror its patterns in your script. You still cannot open files on disk—only use what appears in the snapshot.

Output ONLY a JSON object, no markdown fences, with exactly one key:
  "code": "<python source>"

Rules for the code:
- Allowed imports (stdlib + sandbox): math, json, itertools, functools, collections, statistics, datetime, decimal, fractions, string, random, re, operator, copy, io, base64, csv, hashlib, typing, warnings, plus **numpy**, **pandas**, **matplotlib**, **yfinance** and their usual dependencies (already whitelisted).
- **Matplotlib:** call `import matplotlib; matplotlib.use("Agg")` before `pyplot`. To show a chart in the chat UI, save PNG to bytes and print **exactly one line**: `ADA_IMAGE_PNG:` + base64 (no newlines inside), e.g. `print("ADA_IMAGE_PNG:" + base64.b64encode(buf.getvalue()).decode())`. You may print other text before/after on separate lines; those appear as monospace. Raw single-line PNG base64 (starts with `iVBOR`) is also detected.
- **HTML/SVG/Mermaid preview:** print `ADA_PREVIEW_HTML:<markup>`, `ADA_PREVIEW_SVG:<markup>`, or `ADA_PREVIEW_MERMAID:graph TD; A --> B` on one line (or a ```html / ```svg / ```mermaid fence). The UI shows a sandboxed live preview.
- **yfinance:** OK for pulling `Ticker(...).history(...)` or `fast_info` inside your analysis script when the task needs live series.
- No `open()`, no `os`/`sys`/`subprocess`, no `input()`. Print answers with `print()`.
- Keep code focused; prefer small readable steps.

Example for "factorial of 10":
{"code": "import math\\nprint(math.factorial(10))"}
"""

_CODING_LINKED_PROJECT_NOTE = """
**Linked project folder:** The snapshot below lists real paths and file bodies from the user's open folder (read-only to you).
Your Python **cannot** modify those files on disk. When they ask you to fix, implement, or refactor **project** code,
still output runnable JSON `{"code": "..."}` here for any analysis or helpers; a dedicated step will emit full updated
files for their UI diff/apply flow—do not try to put file contents inside JSON.
"""


def _likely_repo_edit_without_sandbox(goal: str) -> bool:
    """
    Heuristic: task is primarily editing/linking project files, without requiring sandbox plots/compute as the main ask.
    When True, we skip Python execution and go straight to workspace file proposals from the snapshot.
    """
    g = (goal or "").lower().strip()
    if not g:
        return False
    if g.startswith(("what is ", "what are ", "explain ", "describe ", "summarize ", "overview ", "list ")):
        if not any(x in g for x in ("fix", "bug", "change", "update", "implement", "refactor", "add ", "remove ")):
            return False
    analysis_first = (
        "plot ",
        "chart",
        "graph ",
        "histogram",
        "yfinance",
        "correlation",
        "simulate",
        "run a backtest",
        "dataframe",
        "numpy ",
        "matplotlib",
    )
    path_touch = any(
        x in g
        for x in (
            ".tsx",
            ".jsx",
            ".ts",
            ".js",
            ".mjs",
            ".cjs",
            ".py",
            ".css",
            ".scss",
            ".html",
            ".json",
            "src/",
            "app/",
            "components/",
            "pages/",
            "lib/",
            "backend/",
            "frontend/",
            "folder ",
            "repository",
            "codebase",
            "readme",
            "dockerfile",
            "package.json",
            "pyproject.toml",
        )
    )
    repo_signals = (
        "fix ",
        "bug",
        "refactor",
        "add ",
        "remove ",
        "delete ",
        "change ",
        "update ",
        "edit ",
        "modify ",
        "rename ",
        "create file",
        "new file",
        "file ",
        "component",
        "hook",
        "eslint",
        "typescript",
        "broken",
        "doesn't work",
        "does not work",
        "not working",
        "error in",
    )
    implement_touch = "implement" in g and (
        path_touch or "project" in g or "feature" in g or "file" in g or "component" in g or "ui" in g or "api" in g
    )
    has_repo = any(s in g for s in repo_signals) or implement_touch
    has_heavy_analysis = sum(1 for s in analysis_first if s in g) >= 2 and not has_repo
    if has_heavy_analysis:
        return False
    if any(s in g for s in analysis_first) and not has_repo:
        return False
    pure_algo = any(x in g for x in ("factorial", "fibonacci", "leetcode", "sorting algorithm", "binary search"))
    if pure_algo and not path_touch:
        return False
    return has_repo and (
        path_touch
        or implement_touch
        or any(s in g for s in ("fix ", "bug", "broken", "error", "refactor", "update ", "edit ", "add "))
    )


def _propose_workspace_file_fences(
    goal: str,
    project_context: str,
    agent_reply: str,
    api_key: str,
    provider: str,
) -> str:
    """
    LLM pass: emit ```ada-file:...``` blocks with full file bodies. Caller strips fences for chat and sends file_edits to UI.
    """
    ctx = (project_context or "").strip()
    if len(ctx) < _MIN_CTX_FOR_WORKSPACE_PROPOSE:
        return ""
    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")
    system = (
        "You are Jarvis's **workspace file writer**. The user has a project folder open in the app. You receive a markdown "
        "snapshot that lists relative paths and the current contents of many text files (excerpts may be truncated).\n\n"
        "**Job:** Produce the **actual file updates** the user asked for—not suggestions in prose. You must emit the "
        "**complete new text** for every file you change so the app can show a line diff and the user can apply it "
        "to their disk.\n\n"
        "**How to output updates (required format):**\n"
        "- For each file you modify or create, output one markdown fence.\n"
        "- **Opening line** is exactly: ```ada-file:relative/path/from/project/root.ext (then newline). Use forward slashes only.\n"
        "- Next lines: the **entire** file content after your edits (not a patch, not a snippet—full file).\n"
        "- **Closing line**: ``` (three backticks) alone.\n\n"
        "**Rules:**\n"
        "- Prefer paths that **already appear** in the snapshot. If the user asks for a **new** file, pick a sensible path "
        "next to related files.\n"
        "- If the snapshot shows only part of a file, **reconstruct** a consistent full file: keep unchanged regions as they "
        "appear in the snapshot and integrate edits; do not leave placeholders like TODO unless the user asked for them.\n"
        "- Small, purely informational questions about the repo with **no** requested code change → reply exactly: NO_EDITS\n"
        "- Tasks that are **only** Python/math/plots with no repo edit → NO_EDITS\n"
        "- Otherwise, when the user wants code/files fixed or added in their project, you **must** output ada-file blocks.\n\n"
        "Do not wrap ada-file fences inside another outer code fence. No prose before or after the fences except NO_EDITS."
    )
    user = (
        f"## User task (do this in the repo)\n{goal[:4000]}\n\n"
        f"## Repository snapshot (paths + file bodies; use as source of truth)\n{ctx[:20000]}\n\n"
        "## Context from the coding agent turn (may include sandbox output or a short summary)\n"
        f"{(agent_reply or '')[:12000]}\n\n"
        "Output NO_EDITS or only ```ada-file:...``` fences as specified."
    )
    create_kw: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        **chat_completion_limit_kwargs(provider, model, 24000),
    }
    if not should_omit_temperature(provider, model):
        create_kw["temperature"] = 0.1
    try:
        resp = client.chat.completions.create(**create_kw)
        raw = (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""
    if not raw or raw.upper() == "NO_EDITS" or raw.split("\n", 1)[0].strip().upper() == "NO_EDITS":
        return ""
    return "\n\n" + raw


def _workspace_proposals_append(
    goal: str,
    ctx_full: str,
    reply_so_far: str,
    api_key: str,
    provider: str,
    on_step: Optional[Callable] = None,
    *,
    notify_step_index: int = 2,
) -> str:
    """Notify UI, run workspace proposal LLM, return markdown to append (may be empty)."""
    if len(ctx_full) < _MIN_CTX_FOR_WORKSPACE_PROPOSE:
        return ""
    if on_step:
        on_step(
            notify_step_index,
            "Preparing proposed file updates from your project snapshot…",
            "workspace_edits",
            "The model reads paths and bodies from the snapshot and emits full files for the diff panel.",
            None,
            False,
            screenshot_base64=None,
        )
    return _propose_workspace_file_fences(goal, ctx_full, reply_so_far, api_key, provider)


def _parse_code_from_llm(raw: str) -> Optional[str]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        obj = json.loads(text)
        c = obj.get("code")
        if isinstance(c, str) and c.strip():
            return c.strip()
    except json.JSONDecodeError:
        pass
    blocks = extract_python_fences(raw or "")
    if blocks:
        return "\n\n".join(blocks).strip()
    return None


def _swe_workspace_root(explicit: Optional[str] = None) -> str:
    raw = (explicit or "").strip()
    if raw:
        from pathlib import Path

        p = Path(raw).expanduser()
        if p.is_dir():
            return str(p.resolve())
    try:
        from config import get_workspace_root

        linked = (get_workspace_root() or "").strip()
        if linked:
            from pathlib import Path

            p = Path(linked).expanduser()
            if p.is_dir():
                return str(p.resolve())
    except Exception:
        pass
    return ""


def _use_swe_loop(goal: str, workspace_root: str, project_context: str) -> bool:
    """Linked folder + edit/work intent → SWE. Plots/Q&A stay on the sandbox/chat path."""
    if not workspace_root:
        return False
    g = (goal or "").lower().strip()
    if not g:
        return False
    sandbox_first = (
        "plot ",
        "chart",
        "histogram",
        "yfinance",
        "matplotlib",
        "dataframe",
        "numpy ",
        "simulate",
        "run a backtest",
        "correlation",
    )
    if any(s in g for s in sandbox_first) and not _likely_repo_edit_without_sandbox(goal):
        return False
    if _likely_repo_edit_without_sandbox(goal):
        return True
    qa = g.startswith(("what is ", "what are ", "explain ", "describe ", "summarize ", "overview ", "why "))
    if qa and not any(x in g for x in ("fix", "bug", "change", "update", "implement", "refactor", "add ", "remove ", "edit ")):
        return False
    return True


def run_coding_agent(
    goal: str,
    on_step: Optional[Callable] = None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    *,
    project_context: Optional[str] = None,
    workspace_root: Optional[str] = None,
    apply_writes: bool = False,
    run_id: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Repo tasks: localize → patch → test → critic (overlay worktree).
    Compute/plot tasks: generate Python → sandbox → optional one retry.
    Returns (reply_text, tool_used for chat log / UI).
    """
    if api_key is None:
        from config import get_llm_api_key

        api_key = get_llm_api_key()

    if run_id:
        try:
            from agents.run_control import stop_reason

            halt = stop_reason(run_id)
            if halt:
                return halt, {}
        except Exception:
            pass

    goal = (goal or "").strip()
    if not goal:
        return "No task provided.", {}

    ctx_full = (project_context or "").strip()
    if len(ctx_full) > _MAX_PROJECT_CONTEXT_CHARS:
        ctx_full = ctx_full[: _MAX_PROJECT_CONTEXT_CHARS].rstrip() + "\n\n…"

    root = _swe_workspace_root(workspace_root)
    if _use_swe_loop(goal, root, ctx_full):
        from agents.swe_loop import run_swe_loop

        return run_swe_loop(
            goal,
            root,
            on_step=on_step,
            api_key=api_key,
            provider=provider,
            apply_writes=apply_writes,
            project_context=ctx_full,
            control_run_id=run_id,
        )

    if len(ctx_full) > 100 and _likely_repo_edit_without_sandbox(goal):
        plan = (
            "Plan:\n"
            "  1. Use the repository snapshot (from the user's currently open folder) to find paths and current file bodies.\n"
            "  2. Emit **complete updated files** as ada-file markdown fences for the in-app diff and apply flow.\n"
            "  3. Skip the Python sandbox—this task does not require executed analysis code.\n"
        )
        if on_step:
            on_step(0, plan, "plan", plan, None, False, screenshot_base64=None)
        intro = (
            f"**Coding task:** {goal}\n\n"
            "Generating **concrete file updates** from your linked project. When proposals appear, use "
            "**Review file changes** to preview diffs and **apply** them to your folder.\n\n"
        )
        extra = _workspace_proposals_append(
            goal, ctx_full, intro, api_key, provider, on_step, notify_step_index=1
        )
        reply = intro + (
            extra
            if extra
            else "_No file proposals were returned. Name the path, paste the error, or describe the change more specifically._\n"
        )
        if on_step:
            on_step(
                2,
                "Workspace proposals ready." if extra else "No automatic proposals.",
                "done",
                reply[:500] + ("…" if len(reply) > 500 else ""),
                None,
                True,
                screenshot_base64=None,
            )
        return reply, {
            "name": "workspace_file_proposal",
            "input": goal[:4000],
            "result": "ada-file proposals" if extra else "NO_EDITS",
        }

    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")

    plan = (
        "Plan:\n"
        "  1. Interpret the task as computation, data analysis, or visualization (sandbox).\n"
        "  2. Generate Python (stdlib + numpy/pandas/matplotlib/yfinance as needed).\n"
        "  3. Execute in the isolated sandbox and return stdout.\n"
        "  4. If a project snapshot is linked, a follow-up step proposes full-file updates for the diff UI.\n"
    )
    if on_step:
        on_step(0, plan, "plan", plan, None, False, screenshot_base64=None)

    user_msg = f"Task:\n{goal}\n\n"
    if ctx_full:
        user_msg += (
            f"Project repository snapshot (read-only; your code cannot open these paths):\n{ctx_full}\n\n"
            f"{_CODING_LINKED_PROJECT_NOTE.strip()}\n\n"
        )
    user_msg += 'Output ONLY JSON: {"code": "..."}'

    def call_llm(follow_ups: Optional[list[dict]] = None) -> str:
        messages: list[dict] = [
            {"role": "system", "content": _CODING_GEN_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        if follow_ups:
            messages.extend(follow_ups)
        create_kw: dict = {
            "model": model,
            "messages": messages,
            **chat_completion_limit_kwargs(provider, model, 2500),
        }
        if not should_omit_temperature(provider, model):
            create_kw["temperature"] = 0.2
        resp = client.chat.completions.create(**create_kw)
        return (resp.choices[0].message.content or "").strip()

    raw = call_llm()
    code = _parse_code_from_llm(raw)
    if not code:
        reply = (
            f"Coding task (goal: {goal}).\n\n"
            "I could not extract valid Python from the model output. Raw response (truncated):\n"
            f"{raw[:1500]}"
        )
        tool_used = {"name": "python_sandbox", "input": goal[:500], "result": raw[:2000]}
        extra = ""
        if len(ctx_full) >= _MIN_CTX_FOR_WORKSPACE_PROPOSE:
            extra = _workspace_proposals_append(
                goal, ctx_full, reply, api_key, provider, on_step, notify_step_index=1
            )
        if extra:
            reply = reply + extra
            tool_used["workspace_proposals"] = "appended"
        err_step = 2 if len(ctx_full) >= _MIN_CTX_FOR_WORKSPACE_PROPOSE else 1
        if on_step:
            on_step(err_step, "Parse failed", "error", raw[:200], None, True, screenshot_base64=None)
        return reply, tool_used

    if on_step:
        on_step(
            1,
            "Generated Python; executing in sandbox.",
            "sandbox",
            code[:500] + ("…" if len(code) > 500 else ""),
            None,
            False,
            screenshot_base64=None,
        )

    if run_id:
        try:
            from agents.run_control import stop_reason

            halt = stop_reason(run_id)
            if halt:
                return halt, {}
        except Exception:
            pass

    result = run_sandboxed_python(code, timeout_sec=45.0)
    if not result.get("ok"):
        fix_msg = (
            "Your previous code failed in the sandbox. Fix it.\n\n"
            f"Code was:\n```\n{code[:3000]}\n```\n\n"
            f"Sandbox result:\n{json.dumps(redact_sandbox_result_dict(result), indent=2)[:4000]}\n\n"
            'Output ONLY JSON with key "code".'
        )
        raw2 = call_llm(
            [
                {"role": "assistant", "content": raw[:8000]},
                {"role": "user", "content": fix_msg},
            ]
        )
        code2 = _parse_code_from_llm(raw2)
        if code2:
            code = code2
            result = run_sandboxed_python(code, timeout_sec=45.0)

    tool_used = {
        "name": "python_sandbox",
        "input": code[:4000] + ("…" if len(code) > 4000 else ""),
        "result": json.dumps(redact_sandbox_result_dict(result), ensure_ascii=False)[:8000],
    }

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    if result.get("ok"):
        body = "**Sandbox output:**\n\n" + stdout_to_markdown_body(stdout)
        if stderr:
            body += f"\n\n**stderr:**\n```\n{stderr}\n```"
        reply = (
            f"Coding task (goal: {goal}).\n\n"
            f"{body}\n\n"
            "_Executed in the restricted Python sandbox (no desktop automation)._"
        )
        extra = ""
        if len(ctx_full) >= _MIN_CTX_FOR_WORKSPACE_PROPOSE:
            extra = _workspace_proposals_append(
                goal, ctx_full, reply, api_key, provider, on_step, notify_step_index=2
            )
        if extra:
            reply = reply + extra
        if on_step:
            _preview = redact_image_stdout(stdout)
            done_idx = 3 if len(ctx_full) >= _MIN_CTX_FOR_WORKSPACE_PROPOSE else 2
            on_step(
                done_idx,
                "Sandbox run succeeded."
                + (" Proposed project file updates were added—check the file review panel." if extra else ""),
                "done",
                _preview[:300] + ("…" if len(_preview) > 300 else ""),
                _preview[:1200] + ("…" if len(_preview) > 1200 else ""),
                True,
                screenshot_base64=None,
            )
    else:
        err = result.get("error") or "unknown error"
        tb = (result.get("traceback") or "")[:2000]
        reply = (
            f"Coding task (goal: {goal}).\n\n"
            f"Sandbox run failed: **{err}**\n\n"
            f"```\n{tb}\n```"
        )
        extra = ""
        if len(ctx_full) >= _MIN_CTX_FOR_WORKSPACE_PROPOSE:
            extra = _workspace_proposals_append(
                goal, ctx_full, reply, api_key, provider, on_step, notify_step_index=2
            )
        if extra:
            reply = reply + extra
        if on_step:
            done_idx = 3 if len(ctx_full) >= _MIN_CTX_FOR_WORKSPACE_PROPOSE else 2
            on_step(
                done_idx,
                f"Sandbox failed: {err}"
                + (" — file-change proposals were still generated from the snapshot when possible." if extra else ""),
                "done",
                err,
                None,
                True,
                screenshot_base64=None,
            )

    return reply, tool_used
