"""Supervisor agent: decides whether to run agents, which ones (one or more), and goals per step."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client
from tools.shell_runner import is_shell_enabled

MAX_AGENTS_PER_PLAN = 5

_SUPERVISOR_SYSTEM = """You are the Jarvis supervisor. You decide how to handle each user message.

You can assign **one specialist** or **several in sequence** when the task naturally splits (e.g. fetch market data then plot it; run shell prep then coding; desktop navigation then a summary — use your judgment).

Specialists:
1. **chat** – Conversation only (questions, explain, summarize). No code execution or GUI. For web Q&A in prose (there is no separate Playwright browser agent).
2. **desktop** – **GUI** control: mouse and keyboard on the user’s screen (apps, taskbar, visible browser windows). On-screen games and sites included — not coding.
3. **coding** – **Python sandbox** (numpy/pandas/matplotlib, plots) **or linked-repo SWE**: localize files, apply patches, run tests, critic retry. Use coding for bugs, refactors, failing tests, and new project files — not desktop automation.
4. **shell** – **Host terminal** (git, npm, mkdir, PowerShell, bash). Only when they need the real machine shell. If shell is disabled on the server, do not choose **shell**.
5. **finance** – **yfinance + short prose**: quotes, P/E, comparisons without custom code/plots.
6. **google** – **Google Calendar + Gmail** (API): list/create/update/delete calendar events; list/read/send email; labels. Uses the user’s Google sign-in from the app (not desktop GUI).

Reply with ONLY a JSON object, no markdown. Prefer this shape when **multiple** steps are needed:
{
  "run_agent": true,
  "agents": [
    { "agent": "finance", "goal": "concise subtask for this agent" },
    { "agent": "coding", "goal": "next subtask; may refer to prior results" }
  ],
  "reasoning": "one sentence",
  "next_steps": "ordered plan across agents"
}

For a **single** specialist you may use either `agents` with one element **or** the compact form:
{
  "run_agent": true,
  "agent": "desktop" or "coding" or "shell" or "finance" or "google",
  "goal": "one clear sentence",
  "reasoning": "...",
  "next_steps": "..."
}

When **no** agent is needed:
{
  "run_agent": false,
  "agents": [],
  "agent": null,
  "goal": null,
  "reasoning": "...",
  "next_steps": ""
}

Rules:
- Use **multiple** `agents` when steps are sequential and need different capabilities (data fetch → code/chart; shell → code; desktop → chat follow-up is usually one desktop goal unless they asked for two phases).
- **Programming / matplotlib / pandas / sandbox** → **coding** (not desktop).
- **mkdir / git / npm / terminal** → **shell** (not desktop), only if appropriate.
- **Ticker quote + plot** → often **finance** then **coding** in two entries.
- **Calendar / schedule / Gmail / inbox / send email** → **google** (not desktop).
- **Click / play a site or game / chess.com / control the screen** → **desktop**. Never send that to chat.
- Short follow-ups after a Desktop task ("try again", "keep going", "you were just doing that") → **desktop** with the same goal.
- Be decisive. Output only valid JSON."""


def _decision_with_agents(
    agents: list[dict],
    reasoning: str = "",
    next_steps: str = "",
) -> dict:
    if not agents:
        return {
            "run_agent": False,
            "agents": [],
            "agent": None,
            "goal": None,
            "reasoning": (reasoning or "").strip(),
            "next_steps": (next_steps or "").strip(),
        }
    a0 = agents[0]
    return {
        "run_agent": True,
        "agents": agents,
        "agent": a0.get("agent"),
        "goal": a0.get("goal"),
        "reasoning": (reasoning or "").strip(),
        "next_steps": (next_steps or "").strip(),
    }


def _coding_override_for(low: str) -> bool:
    if any(
        k in low
        for k in (
            "factorial",
            "python script",
            "execute python",
            "run python",
            "run a script",
            "write a program",
            "write python",
            "coding task",
            "in python",
            "sandbox",
        )
    ):
        return True
    return ".py" in low and ("run" in low or "execute" in low)


def _shell_reroute_for(low: str) -> bool:
    return any(
        k in low
        for k in (
            "mkdir ",
            "rmdir ",
            "rm -",
            "bash",
            "powershell",
            "pwsh",
            "terminal",
            "wsl ",
            "diskpart",
            "git clone",
            "npm install",
            "pnpm ",
            "which drive",
            "list drives",
            "list disk",
            "get-psdrive",
            "shell command",
        )
    )


def _sanitize_plan_entry(agent: Optional[str], goal: str, signal_text: str) -> Optional[dict[str, str]]:
    """Return {agent, goal} or None. signal_text drives desktop→coding/shell/finance→coding reroutes."""
    a = agent
    if a == "browser":
        return None
    if a not in ("desktop", "coding", "shell", "finance", "google"):
        return None
    g = (goal or "").strip()
    if not g:
        return None
    low = (signal_text or "").lower()
    if a == "desktop" and _coding_override_for(low):
        a = "coding"
    if a == "desktop" and is_shell_enabled() and _shell_reroute_for(low):
        a = "shell"
    if a == "finance" and _finance_quant_coding_signals(low):
        a = "coding"
    if a == "shell" and not is_shell_enabled():
        return None
    return {"agent": a, "goal": g}


def _finance_quant_coding_signals(low: str) -> bool:
    """
    True when the user wants scripted / quantitative / visual analysis on market data
    (coding agent), not a prose + yfinance summary (finance agent).
    """
    if any(
        k in low
        for k in (
            "matplotlib",
            "pyplot",
            "numpy",
            "pandas",
            "dataframe",
            "histogram",
            "scatter plot",
            "scatterplot",
            "regression",
            "correlation matrix",
            "linear regression",
            "logistic regression",
            "plot ",
            "chart ",
            "graph of",
            "rolling mean",
            "rolling average",
            "moving average",
            "bollinger",
            "backtest",
            "monte carlo",
            "sharpe",
            "drawdown",
            "simulate ",
            "simulation of",
            "covariance",
            "heatmap",
            "volatility of returns",
            "standard deviation of returns",
            "ARIMA",
            "cointegration",
        )
    ):
        return True
    if "correlation" in low and any(
        w in low for w in ("stock", "ticker", "equity", "return", "price", "portfolio", "spy", "etf", "nvda", "aapl", "msft")
    ):
        return True
    if "numpy" in low and any(
        w in low for w in ("stock", "ticker", "return", "price", "portfolio", "yfinance", "equity", "etf")
    ):
        return True
    if "pandas" in low and any(
        w in low
        for w in (
            "stock",
            "ticker",
            "return",
            "price",
            "portfolio",
            "yfinance",
            "equity",
            "etf",
            "aapl",
            "msft",
            "nvda",
            "tsla",
            "spy",
            "qqq",
        )
    ):
        return True
    analysis_phrases = (
        "run an analysis",
        "run analysis",
        "do an analysis",
        "code to analyze",
        "python to analyze",
        "analyze with python",
        "statistical analysis",
        "quantitative analysis",
    )
    if any(p in low for p in analysis_phrases) and any(
        w in low
        for w in (
            "stock",
            "ticker",
            "market",
            "return",
            "price",
            "equity",
            "portfolio",
            "etf",
            "spy",
            "yfinance",
            "aapl",
            "msft",
            "nvda",
            "tsla",
        )
    ):
        return True
    return False


def _heuristic_coding_task(message: str) -> Optional[dict]:
    m = (message or "").strip()
    if not m:
        return None
    low = m.lower()
    if re.search(r"https?://\S+", low):
        return None
    if "python.org" in low and "open" in low:
        return None

    if _finance_quant_coding_signals(low):
        return _decision_with_agents(
            [{"agent": "coding", "goal": m}],
            "Heuristic: quantitative or visualization analysis (sandbox), not finance prose-only fetch.",
            "1. Plan analysis 2. Python (numpy/pandas/matplotlib/yfinance) 3. Sandbox run",
        )

    signals: list[tuple[str, bool]] = [
        ("python script", True),
        ("execute a python", True),
        ("execute python", True),
        ("run a python", True),
        ("run python script", True),
        ("run the script", True),
        ("factorial", True),
        ("write python", True),
        ("python code", True),
        ("in the sandbox", True),
        ("coding agent", True),
        (".py", "run" in low or "execute" in low),
        ("calculate", "python" in low),
        ("compute", "python" in low),
    ]
    for phrase, ok in signals:
        if not ok:
            continue
        if phrase in low:
            return _decision_with_agents(
                [{"agent": "coding", "goal": m}],
                "Heuristic: task is code/computation (sandbox), not GUI desktop control.",
                "1. Generate Python for the goal 2. Run in sandbox 3. Return output",
            )
    return None


def _heuristic_shell_task(message: str) -> Optional[dict]:
    if not is_shell_enabled():
        return None
    m = (message or "").strip()
    if not m:
        return None
    low = m.lower()
    hints = [
        "bash ",
        " in bash",
        "run bash",
        "powershell",
        "pwsh ",
        "terminal",
        "shell command",
        "command line",
        "mkdir ",
        "rmdir ",
        "rm -rf",
        "rm -r ",
        "wsl ",
        "diskpart",
        "which drive",
        "list drives",
        "list disk",
        "get-psdrive",
        "git clone",
        "git pull",
        "npm install",
        "pnpm ",
        "brew install",
        "apt install",
        "run in terminal",
        "execute ls",
        "run ls",
        "run dir",
    ]
    for h in hints:
        if h in low:
            return _decision_with_agents(
                [{"agent": "shell", "goal": m}],
                "Heuristic: host terminal / filesystem / package command.",
                "1. Plan safe shell steps 2. Run commands in workdir 3. Summarize output",
            )
    return None


def _heuristic_finance_task(message: str) -> Optional[dict]:
    m = (message or "").strip()
    if not m:
        return None
    low = m.lower()
    if _finance_quant_coding_signals(low):
        return None

    hints = [
        "yfinance",
        "yahoo finance",
        "stock price",
        "share price",
        "market cap",
        "p/e ratio",
        "pe ratio",
        "dividend yield",
        "ticker",
        "nasdaq",
        "nyse",
        "s&p 500",
        "s&p500",
        "etf ",
        " mutual fund",
        "earnings",
        "quarterly revenue",
        "ytd performance",
        "52 week high",
        "52-week",
        "compare aapl",
        "btc-usd",
        "eth-usd",
        "how is nvda",
        "how is tsla",
        "quote for ",
        "price of ",
    ]
    if any(h in low for h in hints):
        return _decision_with_agents(
            [{"agent": "finance", "goal": m}],
            "Heuristic: market/stock data and analysis (yfinance).",
            "1. Resolve tickers 2. Fetch yfinance 3. Analyze",
        )
    if re.search(r"\b[A-Z]{2,5}\b", m) and any(
        w in low for w in ("stock", "stocks", "equity", "trading", "invest", "portfolio", "quote", "chart")
    ):
        return _decision_with_agents(
            [{"agent": "finance", "goal": m}],
            "Heuristic: ticker symbol + investment context.",
            "1. Resolve tickers 2. Fetch yfinance 3. Analyze",
        )
    return None


_DESKTOP_GOAL_RE = re.compile(r"Desktop task \(goal:\s*(.+?)\)\s*\.", re.S)
_CONTINUE_DESKTOP = (
    "try again",
    "keep going",
    "keep playing",
    "continue",
    "you were just",
    "you just did",
    "you just were",
    "do it again",
    "don't stop",
    "dont stop",
    "resume",
    "same thing",
    "do that again",
)
_DESKTOP_HINTS = (
    "chess.com",
    "lichess",
    "play chess",
    "win the game",
    "play online",
    "start playing",
    "click on",
    "click the",
    "on my screen",
    "on the screen",
    "desktop task",
    "use the mouse",
    "move the pieces",
)


def extract_desktop_goal(text: str) -> Optional[str]:
    m = _DESKTOP_GOAL_RE.search(text or "")
    if not m:
        return None
    return (m.group(1) or "").strip()[:800] or None


def _last_desktop_goal(recent_turns: Optional[list]) -> Optional[str]:
    for turn in reversed(recent_turns or []):
        if not isinstance(turn, dict):
            continue
        if (turn.get("role") or "") != "assistant":
            continue
        goal = extract_desktop_goal(str(turn.get("content") or ""))
        if goal:
            return goal
    return None


_NOT_CONTINUE = (
    "thanks",
    "thank you",
    "thx",
    "never mind",
    "nvm",
    "stop",
    "cancel",
    "what model",
    "who are you",
)


def looks_like_task_continuation(message: str) -> bool:
    low = (message or "").strip().lower()
    if not low or len(low) > 180:
        return False
    if any(p in low for p in _NOT_CONTINUE):
        return False
    if any(p in low for p in _CONTINUE_DESKTOP):
        return True
    if low in {"ok", "yes", "go", "again", "more", "continue", "resume", "please"}:
        return True
    return "again" in low or "keep" in low or "that" in low


def _heuristic_desktop_task(
    message: str,
    recent_turns: Optional[list] = None,
    thread: Optional[dict] = None,
) -> Optional[dict]:
    m = (message or "").strip()
    if not m:
        return None
    low = m.lower()
    if any(h in low for h in _DESKTOP_HINTS):
        return _decision_with_agents(
            [{"agent": "desktop", "goal": m}],
            "Heuristic: on-screen click / live game — desktop GUI, not chat.",
            "1. Look at the screen 2. Click the live UI 3. Continue until the goal is done",
        )
    prior = (thread or {}).get("last_goal") or _last_desktop_goal(recent_turns)
    unfinished_desktop = (thread or {}).get("last_route") == "desktop" and (thread or {}).get(
        "last_status"
    ) == "unfinished"
    if looks_like_task_continuation(m) or any(h in low for h in _CONTINUE_DESKTOP):
        if prior:
            return _decision_with_agents(
                [{"agent": "desktop", "goal": prior}],
                "Heuristic: continue the open desktop goal from thread context.",
                "1. Resume GUI control 2. Finish the same goal",
            )
        if unfinished_desktop or "desktop" in " ".join(
            str((t or {}).get("content") or "") for t in (recent_turns or [])
        ).lower():
            return _decision_with_agents(
                [{"agent": "desktop", "goal": m}],
                "Heuristic: user asked to continue desktop work.",
                "1. Resume GUI control 2. Finish the goal",
            )
    if unfinished_desktop and prior and looks_like_task_continuation(m):
        return _decision_with_agents(
            [{"agent": "desktop", "goal": prior}],
            "Heuristic: unfinished desktop goal + short follow-up.",
            "1. Resume GUI control 2. Finish the same goal",
        )
    return None


def _heuristic_google_task(message: str) -> Optional[dict]:
    m = (message or "").strip()
    if not m:
        return None
    low = m.lower()
    hints = [
        "google calendar",
        "my calendar",
        "gcal",
        "calendar event",
        "add event to calendar",
        "create event ",
        "schedule a meeting",
        "schedule meeting",
        "delete event ",
        "cancel event ",
        "reschedule ",
        "what's on my calendar",
        "whats on my calendar",
        "upcoming events",
        "gmail",
        "my gmail",
        "my inbox",
        "unread email",
        "unread mail",
        "send an email",
        "send email to",
        "email to ",
        "compose email",
        "draft an email",
        "mark as read",
        "trash email",
        "archive email",
        "label in gmail",
    ]
    if any(h in low for h in hints):
        return _decision_with_agents(
            [{"agent": "google", "goal": m}],
            "Heuristic: Google Calendar or Gmail (API via user sign-in).",
            "1. Plan Calendar/Gmail operations 2. Execute with OAuth 3. Summarize for user",
        )
    return None


def _parse_agents_array(out: dict[str, Any], user_message: str) -> list[dict[str, str]]:
    raw = out.get("agents")
    if not isinstance(raw, list):
        return []
    seen: list[dict[str, str]] = []
    for item in raw[:MAX_AGENTS_PER_PLAN]:
        if not isinstance(item, dict):
            continue
        a = item.get("agent")
        g = (item.get("goal") or "").strip() or user_message
        ent = _sanitize_plan_entry(a, g, g)
        if ent:
            seen.append(ent)
    return seen


def supervisor_decision(
    api_key: str,
    provider: str,
    user_message: str,
    *,
    llm_user_content: Optional[str] = None,
    recent_turns: Optional[list] = None,
    thread: Optional[dict] = None,
) -> dict:
    """
    Returns dict with:
      run_agent, agents (list of {agent, goal}), agent/goal (first step, backward compat),
      reasoning, next_steps.

    llm_user_content: optional longer prompt for the supervisor model (e.g. project snapshot);
    heuristics and goal fallbacks still use user_message.
    """
    user_message = (user_message or "").strip()
    if not user_message:
        return _decision_with_agents([], "", "")

    if thread is None:
        from memory.thread_context import infer_thread_from_turns

        thread = infer_thread_from_turns(recent_turns)

    llm_body = (llm_user_content or user_message).strip()
    try:
        from agents.execution_policy import gui_policy_for_prompt

        llm_body = gui_policy_for_prompt() + "\n\n" + llm_body
    except Exception:
        pass
    if thread:
        from memory.thread_context import format_thread_context

        pinned = format_thread_context(thread)
        if pinned:
            llm_body = pinned + "\n\nCurrent user message:\n" + llm_body
    if recent_turns:
        bits = []
        for t in recent_turns[-6:]:
            if not isinstance(t, dict):
                continue
            role = (t.get("role") or "?")[:10]
            content = str(t.get("content") or "").replace("\n", " ")[:280]
            if content:
                bits.append(f"{role}: {content}")
        if bits:
            llm_body = "Recent turns:\n" + "\n".join(bits) + "\n\nCurrent user message:\n" + llm_body

    hinted_desk = _heuristic_desktop_task(user_message, recent_turns, thread)
    if hinted_desk:
        return hinted_desk

    hinted = _heuristic_coding_task(user_message)
    if hinted:
        return hinted

    hinted_shell = _heuristic_shell_task(user_message)
    if hinted_shell:
        return hinted_shell

    hinted_finance = _heuristic_finance_task(user_message)
    if hinted_finance:
        return hinted_finance

    hinted_google = _heuristic_google_task(user_message)
    if hinted_google:
        return hinted_google

    from config import get_routing_model

    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = get_routing_model(provider) or getattr(mod, "CHAT_MODEL", "gpt-4o")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SUPERVISOR_SYSTEM},
            {"role": "user", "content": llm_body},
        ],
        **chat_completion_limit_kwargs(provider, model, 500),
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    raw = raw.strip()
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        return _decision_with_agents([], "Could not parse supervisor response.", "")

    reasoning = (out.get("reasoning") or "").strip()
    next_steps = (out.get("next_steps") or "").strip()

    agents_out = _parse_agents_array(out, user_message)

    if not agents_out:
        run_agent = bool(out.get("run_agent"))
        agent = out.get("agent")
        if agent == "browser":
            agent = None
            run_agent = False
        goal = (out.get("goal") or "").strip() or None
        if run_agent and agent:
            if not goal:
                goal = user_message
            ent = _sanitize_plan_entry(agent, goal, user_message)
            if ent:
                agents_out.append(ent)

    if not agents_out:
        if not bool(out.get("run_agent")):
            return _decision_with_agents([], reasoning, next_steps)
        extra = " (Planned agents were invalid or empty.)" if reasoning else "No valid agent plan."
        return _decision_with_agents([], (reasoning + extra).strip(), next_steps)

    return _decision_with_agents(agents_out, reasoning, next_steps)


def compute_supervisor_decision(
    api_key: str,
    provider: str,
    user_message: str,
    *,
    coding_mode: bool = False,
    coding_project_context: str = "",
    recent_turns: Optional[list] = None,
    thread: Optional[dict] = None,
) -> dict:
    """
    Entry point for the router: optionally forces the coding agent (UI coding mode),
    otherwise delegates to supervisor_decision (optionally augmenting the LLM prompt
    when a project snapshot exists without forcing coding).
    """
    um = (user_message or "").strip()
    ctx = (coding_project_context or "").strip()
    if thread is None:
        from memory.thread_context import infer_thread_from_turns

        thread = infer_thread_from_turns(recent_turns)
    desk = _heuristic_desktop_task(um, recent_turns, thread)
    if desk:
        return desk
    if coding_mode and um:
        reasoning = "Coding mode: user routed to the coding agent (sandbox + optional workspace file updates)."
        next_s = (
            "1. Use the project snapshot (open folder) for paths and file bodies. "
            "2. Run Python in the sandbox when analysis, plots, or computation are needed; otherwise propose file updates directly. "
            "3. Emit full files as jarvis-file fences so the UI can diff and the user can apply changes locally."
        )
        if ctx:
            reasoning += " Project folder snapshot is attached for this turn."
        return _decision_with_agents(
            [{"agent": "coding", "goal": um}],
            reasoning,
            next_s,
        )
    return supervisor_decision(api_key, provider, um, recent_turns=recent_turns, thread=thread)
