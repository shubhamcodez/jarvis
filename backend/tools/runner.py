"""
Tool-call layer: every turn gets full conversation history; then we run applicable tools
(weather, etc.) and return their results for the prompt. So: conversation (always) → tool calls → reply.
"""
from __future__ import annotations

from typing import Optional

from .python_sandbox import try_python_sandbox_tool
from .weather import try_weather_tool
from .web_search import try_extract_web_search_query, web_search_tool_block


def run_tools_for_turn(
    message: str,
    recent_turns: Optional[list[dict]] = None,
    web_search_query: Optional[str] = None,
    allowed_tools: Optional[set[str]] = None,
) -> tuple[str, Optional[dict]]:
    """
    Run all applicable tools for this turn (e.g. weather when the user asks about weather/temperature).
    Uses recent_turns so follow-ups (e.g. "exact temperature?") keep the same context (e.g. San Francisco).

    Returns:
        (system_content_from_tools, tool_used)
        - system_content_from_tools: block to add to system prompt (empty string if no tools ran)
        - tool_used: {"name", "input", "result"} for UI/persistence, or None
    """
    recent_turns = recent_turns or []
    system_blocks: list[str] = []
    tool_used = None

    def _allowed(name: str) -> bool:
        return allowed_tools is None or name in allowed_tools

    # Python sandbox: user asked to run fenced ```python``` (pattern models can use)
    if _allowed("python_sandbox"):
        py_tool = try_python_sandbox_tool(message or "")
        if py_tool:
            py_block, tool_used = py_tool
            system_blocks.append(py_block)

    # Web search: explicit query from client or natural phrasing ("search the web for …")
    wq = (web_search_query or "").strip()
    if not wq:
        wq = try_extract_web_search_query(message or "") or ""
    if wq and _allowed("web_search"):
        block, tool_used = web_search_tool_block(wq)
        system_blocks.append(block)

    # Weather tool: when message is about weather/temperature/forecast
    weather_result = try_weather_tool(message or "", recent_turns=recent_turns) if _allowed("weather") else None
    if weather_result:
        location, result = weather_result
        w_tool = {"name": "weather", "input": location, "result": result}
        system_blocks.append(
            f"REAL-TIME WEATHER DATA (you must use this): {result}\n"
            "Answer the user using ONLY this data. Do NOT say you lack access to real-time weather."
        )
        if tool_used is None:
            tool_used = w_tool

    low = (message or "").lower()
    if _allowed("github_issue") and any(k in low for k in ("issue #", "issues/", "implement #", "fix #")):
        try:
            from tools.github_issues import fetch_issue, format_issue_for_prompt

            issue = fetch_issue(message or "")
            if issue.get("ok"):
                system_blocks.append(format_issue_for_prompt(issue))
                if tool_used is None:
                    tool_used = {"name": "github_issue", "input": issue.get("url"), "result": issue.get("title")}
        except Exception:
            pass

    system_content = "\n\n".join(system_blocks) if system_blocks else ""
    return system_content, tool_used
