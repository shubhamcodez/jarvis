"""
HumanEval benchmark: generate completions only. Never exec model output in-process.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .config import ensure_dirs, obs_dir


def run_human_eval_benchmark(
    providers: Optional[list[str]] = None,
    max_problems: int = 5,
) -> dict[str, Any]:
    """
    Collect model completions for HumanEval prompts. Scoring is not done via exec().
    """
    try:
        from config import get_openai_api_key, get_xai_api_key
        from agents.models import get_llm_client
    except Exception:
        return {"error": "config or models not available", "pass_at_1": {}}
    providers = providers or ["openai", "xai"]
    try:
        import datasets

        he = datasets.load_dataset("openai_humaneval", "openai-human-eval")
        problems = list(he["test"])[:max_problems]
    except Exception:
        return {
            "note": "Install: pip install datasets; HumanEval requires openai-human-eval dataset",
            "pass_at_1": {p: None for p in providers},
        }
    results: dict[str, Any] = {}
    for provider in providers:
        try:
            api_key = get_openai_api_key() if provider == "openai" else get_xai_api_key()
        except ValueError:
            results[provider] = None
            continue
        client = get_llm_client(provider)
        completions = 0
        for item in problems:
            prompt = item.get("prompt", "")
            try:
                completion = client.chat(
                    api_key,
                    f"Complete this Python function. Return only the function body.\n{prompt}",
                    attachment_paths=None,
                )
                if (completion or "").strip():
                    completions += 1
            except Exception:
                pass
        results[provider] = {
            "completions": completions,
            "problems": len(problems),
            "note": "pass@1 is not computed in-process; model output is never executed.",
        }
    ensure_dirs()
    out_path = obs_dir() / "optimization" / "human_eval_results.json"
    try:
        out_path.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {"results": results, "pass_at_1": {}}
