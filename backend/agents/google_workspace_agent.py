"""Google Workspace agent: planner → Calendar/Gmail API ops → summarizing LLM.

Uses the same OAuth session as Settings (cookie-backed). Surfaces Calendar + Gmail
actions comparable to typical MCP tool sets (list/create/update/delete events; list/read/send/modify mail).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client, should_omit_temperature
from auth.google_oauth import get_valid_access_token_for_session
from tools.google_workspace import run_google_op, truncate_for_llm

_PLANNER_SYSTEM = """You are the Google Workspace planner for Jarvis. The user wants Google Calendar and/or Gmail actions.
You output ONLY valid JSON (no markdown fences).

Shape:
{
  "operations": [ { ...one op object per entry... } ],
  "plan_summary": "one short sentence"
}

Each operation must include "op" plus the fields for that op (see below). Max 12 operations.

**Ops** (use these exact "op" strings):

1. **calendar_list** — list the user's calendars. No other fields.

2. **events_list** — calendar_id (string, default "primary"), time_min (ISO8601, optional), time_max (ISO8601, optional), max_results (int, default 20).

3. **event_create** — calendar_id, summary, start_datetime, end_datetime (local date-time strings), time_zone (IANA, e.g. "America/New_York"), description (optional string), attendees (optional list of email strings).

4. **event_update** — calendar_id, event_id, fields (object; partial event body e.g. {"summary": "..."} or nested start/end per Google Calendar API).

5. **event_delete** — calendar_id, event_id.

6. **gmail_list** — query (Gmail search, optional), max_results (int, default 15).

7. **gmail_get** — message_id, format one of minimal|full|metadata|raw (default metadata).

8. **gmail_send** — to (email), subject, body (plain text).

9. **gmail_modify** — message_id, add_label_ids (list, optional), remove_label_ids (list, optional). Use standard ids like INBOX, UNREAD, STARRED, TRASH.

10. **gmail_labels_list** — list labels. No other fields.

Rules:
- For "what's on my calendar" / upcoming events: use events_list with time_min = now (UTC Z) and time_max roughly +7 days unless the user specifies otherwise.
- Never invent event_id or message_id; use prior list results in a multi-step plan when the user refers to "the first email" (then you still output explicit ids only if provided in the user message — otherwise prefer gmail_list then gmail_get in separate operations).
- Times: use ISO 8601; for create always set time_zone when using dateTime.
"""

_SUMMARY_SYSTEM = """You are Jarvis. Summarize Google Calendar and Gmail tool results for the user.

Rules:
- Reply in clear **Markdown**.
- For calendar events: list title, start, end, location/description if present.
- For Gmail: subjects, from, snippet; do not fabricate message bodies not shown in the data.
- If any operation returned ok: false, explain briefly (HTTP status / error) and what the user can do (e.g. reconnect Google, check scopes).
- Do not claim an action succeeded if the JSON shows ok false.
"""


def _parse_json_obj(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        o = json.loads(text)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_op_entry(obj: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    op = str(obj.get("op") or obj.get("operation") or "").strip()
    args = {k: v for k, v in obj.items() if k not in ("op", "operation")}
    return op, args


def run_google_workspace_agent(
    goal: str,
    google_session_id: Optional[str],
    on_step: Optional[Callable] = None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    chat_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Plan Calendar/Gmail ops → execute with user OAuth token → LLM summary.
    """
    if api_key is None:
        from config import get_llm_api_key

        api_key = get_llm_api_key()

    goal = (goal or "").strip()
    if not goal:
        return "No request provided.", {}

    if run_id:
        try:
            from agents.run_control import stop_reason

            halt = stop_reason(run_id)
            if halt:
                return halt, {}
        except Exception:
            pass

    token, err = get_valid_access_token_for_session(google_session_id)
    if not token:
        msg = (
            "**Google Workspace**\n\n"
            "You are not signed in to Google, or the session expired.\n\n"
            f"_{err or 'Open Settings → Sign in with Google.'}_"
        )
        return msg, {"name": "google_workspace", "input": goal[:500], "result": err or "no_token"}

    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")

    plan_msg = f"User request:\n{goal}\n\nOutput ONLY the JSON plan object."
    create_kw: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": plan_msg},
        ],
        **chat_completion_limit_kwargs(provider, model, 1200),
    }
    if not should_omit_temperature(provider, model):
        create_kw["temperature"] = 0.2
    raw_plan = client.chat.completions.create(**create_kw)
    plan_text = (raw_plan.choices[0].message.content or "").strip()
    plan = _parse_json_obj(plan_text) or {}

    ops_raw = plan.get("operations")
    if not isinstance(ops_raw, list):
        ops_raw = []
    operations: list[dict[str, Any]] = [x for x in ops_raw if isinstance(x, dict)][:12]

    plan_summary = str(plan.get("plan_summary") or f"{len(operations)} operation(s)").strip()
    if on_step:
        on_step(0, plan_summary, "plan", plan_summary, None, False, screenshot_base64=None)

    results: list[dict[str, Any]] = []
    step_n = 1
    for entry in operations:
        if run_id:
            try:
                from agents.run_control import stop_reason

                halt = stop_reason(run_id)
                if halt:
                    results.append({"op": "stop", "args": {}, "result": {"ok": False, "error": halt}})
                    break
            except Exception:
                pass
        op, args = _extract_op_entry(entry)
        if not op:
            continue
        if on_step:
            on_step(
                step_n,
                f"Running {op}",
                "google_workspace",
                truncate_for_llm({"op": op, "args": args}, 800),
                None,
                False,
                screenshot_base64=None,
            )
        step_n += 1
        from agents.hitl import maybe_gate_google

        def _exec(token=token, op=op, args=args):
            return run_google_op(token, op, args)

        out = maybe_gate_google(op, args, chat_id=chat_id, execute=_exec)
        results.append({"op": op, "args": args, "result": out})
        if isinstance(out, dict) and (out.get("pending_approval") or out.get("blocked")):
            break
        if on_step:
            on_step(
                step_n,
                f"Finished {op}",
                "google_workspace",
                truncate_for_llm(out, 1500),
                None,
                False,
                screenshot_base64=None,
            )
        step_n += 1

    if not results:
        return (
            "**Google Workspace**\n\n"
            "No operations were planned. Try rephrasing (e.g. list this week’s events, show unread email subjects).",
            {"name": "google_workspace", "input": goal[:500], "result": "no_ops"},
        )

    payload = truncate_for_llm(
        {"user_goal": goal, "plan_summary": plan_summary, "results": results},
        28000,
    )
    user_content = f"**User goal:** {goal}\n\n**Tool results (JSON):**\n```json\n{payload}\n```"

    create_kw2: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        **chat_completion_limit_kwargs(provider, model, 5000),
    }
    if not should_omit_temperature(provider, model):
        create_kw2["temperature"] = 0.3
    raw_ans = client.chat.completions.create(**create_kw2)
    reply = (raw_ans.choices[0].message.content or "").strip()
    if not reply:
        reply = "No summary produced."

    header = "**Google Workspace** (Calendar / Gmail)\n\n"
    full_reply = header + reply

    tool_used = {
        "name": "google_workspace",
        "input": goal[:2000],
        "result": payload[:14000],
    }

    if on_step:
        on_step(
            step_n,
            "Done.",
            "done",
            "Google Workspace agent finished.",
            None,
            True,
            screenshot_base64=None,
        )

    return full_reply, tool_used
