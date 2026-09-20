"""Run Ada against local SWE fixtures, HumanEval, and general sandbox tasks."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from benchmarks.catalog import DATA, GENERAL_CASES, SWE_FIXTURES, swe_fixture_path
from tools.diagnostics import run_python_tests
from tools.overlay_workspace import OverlayWorkspace


def _copy_fixture(src: Path) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="ada-bench-"))
    shutil.copytree(
        src,
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("gold", "__pycache__", ".git"),
    )
    return dest


def apply_gold(src: Path, dest: Path) -> None:
    gold = src / "gold"
    if not gold.is_dir():
        return
    for path in gold.rglob("*"):
        if path.is_file():
            rel = path.relative_to(gold)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def score_workspace(work: Path) -> dict[str, Any]:
    ws = OverlayWorkspace(work)
    return run_python_tests(ws)


def run_swe_fixture(
    item: dict[str, Any],
    *,
    api_key: str,
    provider: str,
    apply_gold_only: bool = False,
) -> dict[str, Any]:
    src = swe_fixture_path(item)
    work = _copy_fixture(src)
    t0 = time.time()
    try:
        before = score_workspace(work)
        if apply_gold_only:
            apply_gold(src, work)
            after = score_workspace(work)
            return {
                "id": item["id"],
                "kind": "swe-gold",
                "passed": bool(after.get("passed")),
                "before_passed": bool(before.get("passed")),
                "seconds": round(time.time() - t0, 2),
                "tests": after.get("summary"),
            }
        from agents.swe_loop import run_swe_loop

        reply, tool = run_swe_loop(
            item["goal"],
            str(work),
            api_key=api_key,
            provider=provider,
            apply_writes=True,
        )
        after = score_workspace(work)
        return {
            "id": item["id"],
            "kind": "swe",
            "passed": bool(after.get("passed")),
            "before_passed": bool(before.get("passed")),
            "seconds": round(time.time() - t0, 2),
            "tests": after.get("summary"),
            "reply_head": (reply or "")[:400],
            "tool": tool.get("name"),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_general(item: dict[str, Any], *, api_key: str, provider: str) -> dict[str, Any]:
    from agents.coding_agent import run_coding_agent

    t0 = time.time()
    reply, _ = run_coding_agent(item["goal"], api_key=api_key, provider=provider)
    expect = item.get("expect_contains") or []
    text = reply or ""
    passed = all(str(x) in text for x in expect)
    return {
        "id": item["id"],
        "kind": "general",
        "passed": passed,
        "seconds": round(time.time() - t0, 2),
        "reply_head": text[:400],
    }


def load_humaneval(limit: int) -> list[dict[str, Any]]:
    path = DATA / "HumanEval.jsonl"
    if not path.exists():
        from benchmarks.download import download_humaneval

        download_humaneval()
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            items.append(json.loads(line))
            if len(items) >= limit:
                break
    return items


def _gsm8k_gold(answer: str) -> str:
    if "####" in (answer or ""):
        return (answer.split("####")[-1] or "").strip().replace(",", "")
    return (answer or "").strip()


def run_gsm8k(limit: int, *, api_key: str, provider: str) -> list[dict[str, Any]]:
    from agents.coding_agent import run_coding_agent

    path = DATA / "gsm8k_sample.jsonl"
    if not path.exists():
        from benchmarks.download import download_gsm8k

        download_gsm8k()
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            if not line.strip():
                continue
            item = json.loads(line)
            gold = _gsm8k_gold(item.get("answer") or "")
            t0 = time.time()
            reply, _ = run_coding_agent(
                "Solve this grade-school math problem with Python. Print only the final numeric answer.\n\n"
                + (item.get("question") or ""),
                api_key=api_key,
                provider=provider,
            )
            text = (reply or "").replace(",", "")
            passed = bool(gold) and gold in text
            rec = {
                "id": f"gsm8k/{i}",
                "kind": "gsm8k",
                "passed": passed,
                "gold": gold,
                "seconds": round(time.time() - t0, 2),
                "reply_head": (reply or "")[:300],
            }
            out.append(rec)
            print(f"  gsm8k/{i} passed={passed} gold={gold} {rec['seconds']}s")
    return out


def run_humaneval(limit: int, *, api_key: str, provider: str) -> list[dict[str, Any]]:
    from benchmarks.humaneval_solver import run_humaneval_item

    out = []
    for item in load_humaneval(limit):
        t0 = time.time()
        rec = run_humaneval_item(item, api_key, provider)
        rec["seconds"] = round(time.time() - t0, 2)
        rec["id"] = rec.get("task_id")
        rec["kind"] = "humaneval"
        out.append(rec)
        print(f"  {rec.get('task_id')} passed={rec.get('passed')} {rec.get('seconds')}s")
    return out


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[bool]] = {}
    for r in runs:
        by.setdefault(r.get("kind") or "other", []).append(bool(r.get("passed")))
    return {
        "n": len(runs),
        "pass_rate": (sum(1 for r in runs if r.get("passed")) / len(runs)) if runs else 0.0,
        "by_kind": {k: sum(v) / len(v) if v else 0.0 for k, v in by.items()},
        "failed": [r.get("id") for r in runs if not r.get("passed")],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ada benchmark runner")
    parser.add_argument(
        "--suite",
        choices=("gold", "swe", "general", "humaneval", "gsm8k", "all"),
        default="swe",
    )
    parser.add_argument("--provider", default="")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--only", default="", help="Comma-separated fixture/case ids")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    from config import get_llm_api_key, get_llm_provider

    provider = (args.provider or get_llm_provider() or "xai").strip()
    api_key = "gold" if args.suite == "gold" else get_llm_api_key()

    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    runs: list[dict[str, Any]] = []
    if args.suite in ("gold", "all"):
        print("== gold (fixtures must fail before and pass after official patches) ==")
        for item in SWE_FIXTURES:
            if only and item["id"] not in only:
                continue
            rec = run_swe_fixture(item, api_key=api_key, provider=provider, apply_gold_only=True)
            print(f"  {rec['id']} gold_pass={rec['passed']} buggy_fail={not rec['before_passed']}")
            runs.append(rec)
    if args.suite in ("swe", "all"):
        print("== SWE fixtures (live agent) ==")
        for item in SWE_FIXTURES:
            if only and item["id"] not in only:
                continue
            rec = run_swe_fixture(item, api_key=api_key, provider=provider)
            print(f"  {rec['id']} passed={rec['passed']} {rec['seconds']}s")
            runs.append(rec)
    if args.suite in ("general", "all"):
        print("== general / sandbox ==")
        for item in GENERAL_CASES:
            if only and item["id"] not in only:
                continue
            rec = run_general(item, api_key=api_key, provider=provider)
            print(f"  {rec['id']} passed={rec['passed']} {rec['seconds']}s")
            runs.append(rec)
    if args.suite in ("humaneval", "all"):
        print("== HumanEval ==")
        runs.extend(run_humaneval(args.limit, api_key=api_key, provider=provider))
    if args.suite in ("gsm8k", "all"):
        print("== GSM8K ==")
        runs.extend(run_gsm8k(args.limit, api_key=api_key, provider=provider))

    summary = summarize(runs)
    print(json.dumps(summary, indent=2))
    payload = {"summary": summary, "runs": runs}
    out = Path(args.out) if args.out else DATA / f"last_run_{args.suite}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")
    return 0 if summary.get("pass_rate", 0) >= 0.5 or args.suite == "gold" else 1


if __name__ == "__main__":
    raise SystemExit(main())
