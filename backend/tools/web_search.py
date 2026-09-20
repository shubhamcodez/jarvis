"""Web search via DuckDuckGo (no API key). Used by chat tools and agent goals."""
from __future__ import annotations

import re
from typing import Optional

_MAX_SNIPPET = 320
_DEFAULT_RESULTS = 10
_MAX_RESULTS_CAP = 12

_EXTRACT_PATTERNS = [
    re.compile(r"(?is)^\s*search\s+(?:the\s+)?web\s+for\s+(.+)$"),
    re.compile(r"(?is)^\s*web\s*search\s*:\s*(.+)$"),
    re.compile(r"(?is)^\s*google\s+search\s*:\s*(.+)$"),
]


def try_extract_web_search_query(message: str) -> Optional[str]:
    """If the user message is clearly a web-search command, return the query string."""
    text = (message or "").strip()
    if not text:
        return None
    for rx in _EXTRACT_PATTERNS:
        m = rx.match(text)
        if m:
            q = (m.group(1) or "").strip()
            return q or None
    return None


def _effective_max_results(requested: int) -> int:
    """Clamp to a sensible band (default ~10, cap 12). Override with ADA_WEB_SEARCH_MAX_RESULTS."""
    import os

    try:
        raw = (os.environ.get("JARVIS_WEB_SEARCH_MAX_RESULTS") or os.environ.get("ADA_WEB_SEARCH_MAX_RESULTS") or "").strip()
        env_n = int(raw or str(_DEFAULT_RESULTS))
    except ValueError:
        env_n = _DEFAULT_RESULTS
    env_n = max(5, min(env_n, _MAX_RESULTS_CAP))
    n = min(requested, env_n)
    return max(1, min(n, _MAX_RESULTS_CAP))


def search_web(query: str, max_results: int = _DEFAULT_RESULTS) -> str:
    """
    Run a text web search; return a plain-text block for the model (titles, URLs, snippets).
    Typically the top 5–10 results (default 10, max 12 unless env overrides default).
    """
    q = (query or "").strip()
    if not q:
        return "Empty search query."

    n_results = _effective_max_results(max_results)

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # legacy package name
        except ImportError:
            return (
                "Web search unavailable: install backend dependency "
                "`ddgs` (e.g. pip install ddgs)."
            )

    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        def _search() -> list:
            ddgs = DDGS()
            return list(ddgs.text(q, max_results=n_results))

        with ThreadPoolExecutor(max_workers=1) as pool:
            hits = pool.submit(_search).result(timeout=15)
    except FuturesTimeout:
        return f"Web search timed out for: {q!r}"
    except Exception as e:
        return f"Web search failed: {e}"

    if not hits:
        return f"No web results found for: {q!r}"

    lines: list[str] = [f"Query: {q}\n"]
    for i, r in enumerate(hits, 1):
        title = (r.get("title") or "").strip()
        href = (r.get("href") or r.get("url") or "").strip()
        body = (r.get("body") or "").strip().replace("\n", " ")
        if len(body) > _MAX_SNIPPET:
            body = body[: _MAX_SNIPPET - 1] + "…"
        lines.append(f"{i}. {title}\n   URL: {href}\n   {body}")
    return "\n\n".join(lines)


def web_search_tool_block(query: str) -> tuple[str, dict]:
    """System prompt block + tool_used dict for logging/UI."""
    text = search_web(query)
    block = (
        "WEB SEARCH RESULTS (top snippets from the live web for this query; use these facts, "
        "cite URLs when relevant, do not invent sources):\n"
        + text
    )
    tool_used = {"name": "web_search", "input": query.strip(), "result": text[:20000]}
    return block, tool_used


def augment_goal_with_web_search(state: dict) -> tuple[str, Optional[dict]]:
    """
    If the request includes a web search query (API field or natural language), prepend results to the agent goal.
    Returns (goal_for_agent, tool_used or None).
    """
    msg = (state.get("message") or "").strip()
    goal = (state.get("goal") or "").strip() or msg
    wq = (state.get("web_search_query") or "").strip()
    if not wq:
        wq = try_extract_web_search_query(msg) or ""
    if not wq:
        return goal, None
    text = search_web(wq)
    block = (
        "WEB SEARCH RESULTS (ground your work in these; cite URLs when relevant):\n"
        f"{text}\n\n---\n\nTASK / USER REQUEST:\n"
    )
    return block + goal, {"name": "web_search", "input": wq, "result": text[:20000]}
