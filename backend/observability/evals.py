"""
Eval store: multi-turn eval cases (messages + expected/rubric) and run results.
LLM-based eval generation from logs lives in eval_gen.py; runner in eval_runner.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import ensure_dirs, obs_dir

CASES_FILE = "eval_cases.jsonl"
RUNS_FILE = "eval_runs.jsonl"


def _cases_path() -> Path:
    ensure_dirs()
    return obs_dir() / "evals" / CASES_FILE


def _runs_path() -> Path:
    ensure_dirs()
    return obs_dir() / "evals" / RUNS_FILE


class EvalCase:
    """One multi-turn eval: list of (role, content) messages + expected outcome or rubric."""
    def __init__(
        self,
        id: str,
        messages: list[dict[str, str]],
        expected: Optional[str] = None,
        rubric: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ):
        self.id = id
        self.messages = messages  # [{"role": "user"|"assistant", "content": "..."}]
        self.expected = expected
        self.rubric = rubric
        self.meta = meta or {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "messages": self.messages,
            "expected": self.expected,
            "rubric": self.rubric,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        return cls(
            id=d.get("id", ""),
            messages=d.get("messages", []),
            expected=d.get("expected"),
            rubric=d.get("rubric"),
            meta=d.get("meta"),
        )


class EvalRun:
    """Result of running one eval case with one model."""
    def __init__(
        self,
        case_id: str,
        provider: str,
        reply: str,
        score: Optional[float] = None,
        passed: Optional[bool] = None,
        error: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ):
        self.case_id = case_id
        self.provider = provider
        self.reply = reply
        self.score = score
        self.passed = passed
        self.error = error
        self.meta = meta or {}

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "provider": self.provider,
            "reply": self.reply,
            "score": self.score,
            "passed": self.passed,
            "error": self.error,
            "meta": self.meta,
        }


def load_eval_cases(limit: int = 1000) -> list[EvalCase]:
    path = _cases_path()
    if not path.exists():
        return []
    cases = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cases.append(EvalCase.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception:
        pass
    if limit and len(cases) > limit:
        cases = cases[-limit:]
    return cases


def save_eval_cases(cases: list[EvalCase]) -> None:
    ensure_dirs()
    path = _cases_path()
    with open(path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")


def append_eval_case(case: EvalCase) -> None:
    ensure_dirs()
    path = _cases_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")


def append_eval_run(run: EvalRun) -> None:
    ensure_dirs()
    path = _runs_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(run.to_dict(), ensure_ascii=False) + "\n")


def load_eval_runs(limit: int = 2000) -> list[dict]:
    path = _runs_path()
    if not path.exists():
        return []
    runs = []
    try:
        data = path.read_text(encoding="utf-8")
        raw = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if limit and len(raw) > limit:
            raw = raw[-limit:]
        for line in raw:
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return runs
