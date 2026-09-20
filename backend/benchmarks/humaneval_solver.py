"""HumanEval / MBPP-style: complete a function, then execute the official check()."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from agents.models import chat_completion_limit_kwargs, get_llm_client, should_omit_temperature
from tools.python_sandbox import run_sandboxed_python

_SYSTEM = """You complete Python function prompts for HumanEval-style tests.

Return ONLY the full function source (the prompt plus your completion), no markdown fences,
no explanation. Do not include the test harness. Implement the function described by the prompt.
"""


def extract_code(raw: str) -> str:
    text = (raw or "").strip()
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if blocks:
        return blocks[0].strip()
    return text


def complete_function(prompt: str, api_key: str, provider: str) -> str:
    mod = get_llm_client(provider)
    client = mod._client(api_key)
    model = getattr(mod, "CHAT_MODEL", "gpt-4o")
    kw: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        **chat_completion_limit_kwargs(provider, model, 1800),
    }
    if not should_omit_temperature(provider, model):
        kw["temperature"] = 0.0
    resp = client.chat.completions.create(**kw)
    return extract_code(resp.choices[0].message.content or "")


def run_humaneval_item(
    item: dict[str, Any],
    api_key: str,
    provider: str,
    completion: Optional[str] = None,
) -> dict[str, Any]:
    prompt = item.get("prompt") or ""
    test = item.get("test") or ""
    entry = item.get("entry_point") or ""
    if completion is None:
        completion = complete_function(prompt, api_key, provider)
    code = completion.strip()
    if "def " not in code:
        code = prompt + "\n" + code
    harness = (
        code
        + "\n\n"
        + test
        + f"\n\ncheck({entry})\nprint('ADA_HE_PASS')\n"
    )
    result = run_sandboxed_python(harness, timeout_sec=20.0)
    passed = bool(result.get("ok")) and "ADA_HE_PASS" in (result.get("stdout") or "")
    return {
        "task_id": item.get("task_id"),
        "passed": passed,
        "ok": result.get("ok"),
        "stdout": (result.get("stdout") or "")[:1500],
        "stderr": (result.get("stderr") or "")[:800],
        "error": result.get("error"),
        "completion": code[:4000],
    }
