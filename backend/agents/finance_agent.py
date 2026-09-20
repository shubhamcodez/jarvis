"""Finance agent: plan tickers + yfinance fetch → LLM analysis of the user's question."""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client, should_omit_temperature
from tools.finance_data import fetch_finance_bundle

_PLAN_SYSTEM = """You are a finance data planner for Ada. The user wants stock/market **data** answered in prose; data will be fetched with **yfinance** (Yahoo Finance). Custom plots, regressions, or heavy Python analysis are handled by the **coding** agent, not here—still plan tickers if this request reached you.

Output ONLY a JSON object (no markdown), shape:
{
  "tickers": ["AAPL"],
  "history_period": "6mo",
  "history_interval": "1d",
  "include_financials": false,
  "restated_question": "what to analyze in one sentence"
}

Rules:
- **tickers**: 1–8 Yahoo Finance symbols (uppercase), e.g. AAPL, MSFT, SPY, QQQ, BTC-USD. Guess symbols from the user message; if they ask about "the market" broadly, use SPY or QQQ.
- **history_period**: one of 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max (default 6mo).
- **history_interval**: usually 1d for daily bars; 1h only for short periods.
- **include_financials**: true if they ask for fundamentals, revenue, margins, balance sheet, quarterly results, P/E context beyond a simple quote.
- **restated_question**: the analysis question to answer after data is loaded.

Example: User "Compare NVDA and AMD YTD performance" → tickers ["NVDA","AMD"], history_period "ytd", include_financials false, restated_question "Compare YTD price performance of NVDA vs AMD."
"""

_ANALYSIS_SYSTEM = """You are a financial analysis assistant. You receive **structured market data** from yfinance (trimmed). The user's question is below.

Rules:
- Base **numbers** (prices, ratios, highs/lows) **only** on the provided data. Say clearly if something is missing or the fetch failed.
- Write a clear **Markdown** answer: short intro, bullets or a small table where useful (no matplotlib; for charts suggest using the **coding** agent in a follow-up if relevant).
- For comparisons, align metrics side-by-side.
- Do **not** invent current prices or ratios not present in the JSON.
- End with: *This is not financial advice.*

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


def _tickers_from_text(goal: str) -> list[str]:
    """Fallback: uppercase tokens that look like tickers (2–5 letters)."""
    found = re.findall(r"\b([A-Z]{2,5})\b", goal or "")
    skip = {"THE", "AND", "FOR", "NOT", "ARE", "BUT", "YOU", "ALL", "CAN", "HER", "WAS", "ONE", "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW", "ITS", "LET", "MAY", "NEW", "NOW", "OLD", "SEE", "TWO", "WHO", "BOY", "DID", "EPS", "YTD", "ETF"}
    out = []
    for t in found:
        if t in skip:
            continue
        if t not in out:
            out.append(t)
        if len(out) >= 4:
            break
    return out


def run_finance_agent(
    goal: str,
    on_step: Optional[Callable] = None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    run_id: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Plan → yfinance fetch → analysis LLM.
    Returns (reply_markdown, tool_used).
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
        return "No question provided.", {}

    try:
        import yfinance as yf  # noqa: F401
    except ImportError:
        return (
            "The **yfinance** package is not installed. From `backend`: `pip install yfinance` or `poetry add yfinance`.",
            {"name": "finance", "input": goal[:500], "result": "yfinance missing"},
        )

    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")

    plan_msg = f"User request:\n{goal}\n\nOutput ONLY the JSON planner object."
    create_kw_plan: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": plan_msg},
        ],
        **chat_completion_limit_kwargs(provider, model, 800),
    }
    if not should_omit_temperature(provider, model):
        create_kw_plan["temperature"] = 0.2
    raw_plan = client.chat.completions.create(**create_kw_plan)
    plan_text = (raw_plan.choices[0].message.content or "").strip()
    plan = _parse_json_obj(plan_text) or {}

    tickers = plan.get("tickers") if isinstance(plan.get("tickers"), list) else []
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()][:8]
    if not tickers:
        tickers = _tickers_from_text(goal)
    if not tickers:
        return (
            "I need a ticker symbol to pull market data (for example AAPL or SPY). "
            "This request did not name one.",
            {"name": "finance", "input": goal[:500], "result": "no_ticker"},
        )

    period = str(plan.get("history_period") or "6mo").strip()
    interval = str(plan.get("history_interval") or "1d").strip()
    include_fin = bool(plan.get("include_financials"))
    restated = str(plan.get("restated_question") or goal).strip()

    plan_summary = (
        f"Plan: tickers {tickers}, period={period}, interval={interval}, "
        f"financials={include_fin}. Q: {restated}"
    )
    if on_step:
        on_step(0, plan_summary, "plan", plan_summary, None, False, screenshot_base64=None)

    if run_id:
        try:
            from agents.run_control import stop_reason

            halt = stop_reason(run_id)
            if halt:
                return halt, {}
        except Exception:
            pass

    bundle = fetch_finance_bundle(
        tickers,
        history_period=period,
        history_interval=interval,
        include_financials=include_fin,
    )
    bundle_json = json.dumps(bundle, ensure_ascii=False, default=str)
    if on_step:
        on_step(
            1,
            "Fetched yfinance data.",
            "finance",
            f"Loaded {len(bundle.get('tickers') or [])} ticker(s).",
            None,
            False,
            screenshot_base64=None,
        )

    user_analysis = (
        f"**User question (restated):** {restated}\n\n"
        f"**Original message:** {goal}\n\n"
        f"**yfinance data (JSON):**\n```json\n{bundle_json[:8000]}\n```"
    )
    create_kw2: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ANALYSIS_SYSTEM},
            {"role": "user", "content": user_analysis},
        ],
        **chat_completion_limit_kwargs(provider, model, 6000),
    }
    if not should_omit_temperature(provider, model):
        create_kw2["temperature"] = 0.3
    raw_ans = client.chat.completions.create(**create_kw2)
    reply = (raw_ans.choices[0].message.content or "").strip()
    if not reply:
        reply = "No analysis produced."

    header = f"**Finance analysis** (yfinance)\n\n"
    full_reply = header + reply

    tool_used = {
        "name": "yfinance",
        "input": {"tickers": tickers, "period": period, "question": restated[:2000]},
        "result": bundle_json[:12000],
    }

    if on_step:
        on_step(2, "Analysis complete.", "done", "Finance agent finished.", None, True, screenshot_base64=None)

    return full_reply, tool_used
