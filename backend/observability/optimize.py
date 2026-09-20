"""
Auto-optimization from logs and eval runs: aggregate success rates, token usage, pass@k per model;
suggest prompt/param tweaks; LLM-generated prompt modification instructions and code addition suggestions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .config import OPT_DIR, ensure_dirs
from .trace import list_traces
from .evals import load_eval_runs
from .eval_runner import pass_at_k


STATS_FILE = "optimization_stats.json"

OPTIMIZER_SYSTEM = """You are an optimization analyst for an Ada-style agent system with:
- A supervisor (LLM decides: chat vs desktop vs coding vs shell vs finance agent)
- Desktop agent (screenshot + vision LLM → mouse + keyboard: click/double/right-click, type, press keys, hotkeys, scroll via pyautogui)
- Coding agent (LLM writes Python → sandbox: stdlib + numpy/pandas/matplotlib/yfinance; no GUI)
- Shell agent (LLM proposes host terminal commands; opt-in via ADA_ENABLE_SHELL)
- Finance agent (planner LLM → yfinance fetch → analyst LLM; stocks/ETFs/crypto tickers)
- Chat (plain LLM reply)

Given trace stats (success rate, errors, tokens per provider) and eval pass@1 per model, and optionally sample failed runs or errors, output a JSON object with two arrays:

1. "prompt_modification_instructions": list of objects with:
   - "target": "supervisor" | "desktop" | "coding" | "shell" | "finance" | "chat" (which prompt to modify)
   - "instruction": concrete text to add or change in that prompt (e.g. "Add: If the user message is ambiguous, prefer chat and ask for clarification.")
   - "reason": one sentence why (e.g. "Eval failures showed confusion on vague goals.")

2. "code_addition_suggestions": list of objects with:
   - "file": path hint (e.g. "backend/agents/desktop_agent.py", "backend/agents/supervisor.py")
   - "suggestion": concrete code or logic to add (e.g. "Add retry up to 2 times on click timeout.")
   - "reason": one sentence why (e.g. "Trace errors showed frequent click timeouts.")

Keep each list to 2–5 items. Be specific and actionable. If there is not enough signal, return empty arrays. Output ONLY the JSON object, no markdown."""


def _stats_path() -> Path:
    ensure_dirs()
    return OPT_DIR / STATS_FILE


def aggregate_trace_stats(trace_limit: int = 1000) -> dict[str, Any]:
    """Per-provider: success rate, total tokens (in/out), error count, avg duration."""
    traces = list_traces(limit=trace_limit)
    by_provider = {}
    for t in traces:
        p = t.get("provider") or "unknown"
        if p not in by_provider:
            by_provider[p] = {"success": 0, "total": 0, "token_in": 0, "token_out": 0, "errors": 0, "duration_sum": 0.0}
        by_provider[p]["total"] += 1
        if t.get("success"):
            by_provider[p]["success"] += 1
        else:
            by_provider[p]["errors"] += 1
        by_provider[p]["token_in"] += t.get("token_input") or 0
        by_provider[p]["token_out"] += t.get("token_output") or 0
        d = t.get("duration_sec")
        if d is not None:
            by_provider[p]["duration_sum"] += float(d)
            by_provider[p]["duration_n"] = by_provider[p].get("duration_n", 0) + 1
    out = {}
    for p, v in by_provider.items():
        n = v["total"]
        dn = v.get("duration_n") or 0
        out[p] = {
            "success_rate": v["success"] / n if n else 0,
            "total_runs": n,
            "token_input_total": v["token_in"],
            "token_output_total": v["token_out"],
            "error_count": v["errors"],
            "avg_duration_sec": v["duration_sum"] / dn if dn else None,
        }
    return out


def aggregate_eval_pass(run_limit: int = 2000) -> dict[str, float]:
    """Pass rate per provider from eval runs (pass@1)."""
    runs = load_eval_runs(limit=run_limit)
    return pass_at_k(runs, k=1)


def _generate_prompt_and_code_suggestions(
    trace_stats: dict[str, Any],
    eval_pass: dict[str, float],
    sample_failed_runs: list[dict],
    sample_trace_errors: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Call LLM to get prompt modification instructions and code addition suggestions. Returns (prompt_instructions, code_suggestions)."""
    try:
        from config import get_llm_api_key, get_llm_provider
        from agents.models import get_llm_client
    except Exception:
        return [], []
    provider = get_llm_provider()
    api_key = get_llm_api_key()
    client = get_llm_client(provider)
    user = (
        f"{OPTIMIZER_SYSTEM}\n\n---\n\n"
        f"Trace stats (per provider): {json.dumps(trace_stats, ensure_ascii=False)}\n\n"
        f"Eval pass@1 per provider: {json.dumps(eval_pass, ensure_ascii=False)}\n\n"
    )
    if sample_failed_runs:
        user += f"Sample failed or low-scoring eval runs (last 5): {json.dumps(sample_failed_runs[:5], ensure_ascii=False)}\n\n"
    if sample_trace_errors:
        user += f"Sample trace errors (last 5): {json.dumps(sample_trace_errors[:5], ensure_ascii=False)}\n\n"
    user += "Output the JSON object with prompt_modification_instructions and code_addition_suggestions."
    try:
        raw = client.chat(api_key, user, attachment_paths=None)
        raw = (raw or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
        out = json.loads(raw)
        prompts = out.get("prompt_modification_instructions")
        code = out.get("code_addition_suggestions")
        if not isinstance(prompts, list):
            prompts = []
        if not isinstance(code, list):
            code = []
        return prompts, code
    except Exception:
        return [], []


def run_optimization_step() -> dict[str, Any]:
    """
    One optimization step: aggregate traces + eval runs, write stats, then ask LLM for
    prompt modification instructions and code addition suggestions. Return full summary.
    """
    trace_stats = aggregate_trace_stats()
    eval_pass = aggregate_eval_pass()
    summary = {
        "trace_stats": trace_stats,
        "eval_pass_at_1": eval_pass,
        "suggestions": [],
        "prompt_modification_instructions": [],
        "code_addition_suggestions": [],
    }
    # Simple suggestions from heuristics
    for provider, ts in trace_stats.items():
        if ts.get("success_rate") is not None and ts["success_rate"] < 0.8:
            summary["suggestions"].append({
                "provider": provider,
                "type": "prompt_or_params",
                "reason": f"Success rate {ts['success_rate']:.2f} below 0.8",
            })
    if eval_pass:
        best = max(eval_pass.items(), key=lambda x: x[1])
        worst = min(eval_pass.items(), key=lambda x: x[1])
        if best[1] - worst[1] > 0.1:
            summary["suggestions"].append({
                "type": "benchmark",
                "best_model": best[0],
                "worst_model": worst[0],
                "pass_gap": best[1] - worst[1],
            })

    # Sample failed eval runs (passed=False or low score) and trace errors for LLM context
    runs = load_eval_runs(limit=100)
    sample_failed_runs = [r for r in runs if r.get("passed") is False or (r.get("score") is not None and float(r.get("score", 1)) < 0.5)]
    traces = list_traces(limit=200)
    sample_trace_errors = [t for t in traces if t.get("success") is False]
    prompt_instructions, code_suggestions = _generate_prompt_and_code_suggestions(
        trace_stats, eval_pass, sample_failed_runs, sample_trace_errors
    )
    summary["prompt_modification_instructions"] = prompt_instructions
    summary["code_addition_suggestions"] = code_suggestions

    path = _stats_path()
    try:
        path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return summary


def get_latest_optimization_stats() -> Optional[dict[str, Any]]:
    path = _stats_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
