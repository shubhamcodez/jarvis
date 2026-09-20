"""Shell agent: LLM proposes host shell commands (opt-in); stdout/stderr fed back until done."""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client, should_omit_temperature
from tools.shell_runner import is_shell_enabled, run_shell_command, shell_runtime_label


_SHELL_SYSTEM_TEMPLATE = """You are Ada's shell agent. The user wants work done on the **real machine** using the system shell (not the Python sandbox).

Runtime: {runtime}

You MUST output ONLY a JSON object (no markdown fences), shape:
{{
  "done": true or false,
  "command": "a single shell command to run next, or empty string if done",
  "thought": "brief reasoning for the user/UI"
}}

Rules:
- One command per turn. Keep commands short. Prefer safe, incremental steps.
- Default working directory is the Ada shell workdir (see runtime). Use `cd` only when needed; note that each invocation may reset cwd depending on shell — prefer absolute paths under the workdir when possible.
- On Windows with PowerShell, use PowerShell syntax (e.g. Get-PSDrive, New-Item, Remove-Item). With bash, use bash syntax.
- When the task is finished, set "done": true and "command": "".
- Never ask for passwords or run interactive prompts; use non-interactive flags.
- If a command fails, analyze stderr and try a corrected command, or explain in "thought" and set done true if impossible.

Example (bash): {{"done": false, "command": "ls -la", "thought": "List files in workdir."}}
Example (done): {{"done": true, "command": "", "thought": "Created folder and verified listing."}}
"""


def _parse_shell_json(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def run_shell_agent(
    goal: str,
    on_step: Optional[Callable] = None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    max_steps: int = 15,
    chat_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Multi-turn: model proposes command → run on host → feed output back.
    Returns (reply_text, tool_used for chat log).
    """
    if api_key is None:
        from config import get_llm_api_key

        api_key = get_llm_api_key()

    goal = (goal or "").strip()
    if not goal:
        return "No task provided.", {}

    if not is_shell_enabled():
        return (
            "Host shell is **disabled** on this server (`ADA_ENABLE_SHELL=0` or `ADA_DISABLE_SHELL=1`).",
            {"name": "shell", "input": goal[:500], "result": "disabled"},
        )

    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")
    runtime = shell_runtime_label()
    system = _SHELL_SYSTEM_TEMPLATE.format(runtime=runtime)

    plan = (
        f"Plan (shell agent, {runtime}):\n"
        "  1. Break the goal into safe shell steps.\n"
        "  2. Run one command per turn; read stdout/stderr.\n"
        "  3. Finish with a clear summary for the user.\n"
    )
    if on_step:
        on_step(0, plan, "plan", plan, None, False, screenshot_base64=None)

    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Task:\n{goal}\n\nOutput ONLY JSON as specified."},
    ]

    def call_llm() -> str:
        create_kw: dict = {
            "model": model,
            "messages": messages,
            **chat_completion_limit_kwargs(provider, model, 4000),
        }
        if not should_omit_temperature(provider, model):
            create_kw["temperature"] = 0.2
        resp = client.chat.completions.create(**create_kw)
        return (resp.choices[0].message.content or "").strip()

    transcript: list[str] = []
    last_tool_result = ""

    for step_i in range(1, max_steps + 1):
        if run_id:
            try:
                from agents.run_control import is_cancelled

                if is_cancelled(run_id):
                    transcript.append("Stopped by user.")
                    break
            except Exception:
                pass
        raw = call_llm()
        data = _parse_shell_json(raw) or {}
        thought = str(data.get("thought") or "").strip()
        done = bool(data.get("done"))
        command = str(data.get("command") or "").strip()

        if on_step:
            on_step(
                step_i,
                thought or "Shell step",
                "shell",
                (command or "(finishing)")[:500],
                None,
                False,
                screenshot_base64=None,
            )

        if done or not command:
            summary = thought or raw[:1500]
            reply = f"**Shell task** (goal: {goal})\n\n{summary}\n"
            if transcript:
                reply += "\n**Command log:**\n" + "\n".join(transcript)
            tool_used = {
                "name": "shell",
                "input": goal[:2000],
                "result": (last_tool_result or summary)[:8000],
            }
            if on_step:
                on_step(step_i + 1, summary[:200], "done", summary[:400], summary[:1500], True, screenshot_base64=None)
            return reply.strip(), tool_used

        from agents.hitl import maybe_gate_shell

        result = maybe_gate_shell(
            command,
            chat_id=chat_id,
            execute=lambda cmd=command: run_shell_command(cmd),
        )
        if result.get("pending_approval"):
            last_tool_result = json.dumps(result, ensure_ascii=False)[:8000]
            reply = (
                f"**Shell task** (goal: {goal})\n\n"
                f"Paused for confirmation: `{command}`\n\n"
                "Approve or deny this command in the Ada window, then retry if needed."
            )
            if transcript:
                reply += "\n\n**Command log:**\n" + "\n".join(transcript)
            if on_step:
                on_step(
                    step_i + 1,
                    "Awaiting approval",
                    "hitl",
                    result.get("error") or "pending",
                    last_tool_result,
                    True,
                    screenshot_base64=None,
                )
            return reply, {
                "name": "shell",
                "input": goal[:2000],
                "result": last_tool_result,
            }
        last_tool_result = json.dumps(result, ensure_ascii=False)[:8000]
        line = f"- `$ {command}` → exit {result.get('returncode')}\n  stdout:\n```\n{(result.get('stdout') or '')[:2000]}\n```"
        if result.get("stderr"):
            line += f"\n  stderr:\n```\n{(result.get('stderr') or '')[:1500]}\n```"
        if result.get("error"):
            line += f"\n  error: {result.get('error')}"
        transcript.append(line)

        messages.append({"role": "assistant", "content": raw[:8000]})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Command result (JSON):\n"
                    + json.dumps(result, ensure_ascii=False)[:12000]
                    + "\n\nOutput the next JSON object (done/command/thought)."
                ),
            }
        )

    reply = (
        f"**Shell task** (goal: {goal})\n\nStopped after {max_steps} steps (limit).\n\n"
        + "\n".join(transcript)
    )
    tool_used = {
        "name": "shell",
        "input": goal[:2000],
        "result": last_tool_result[:8000],
    }
    if on_step:
        on_step(max_steps + 1, "Step limit", "done", reply[:300], reply[:1500], True, screenshot_base64=None)
    return reply, tool_used
