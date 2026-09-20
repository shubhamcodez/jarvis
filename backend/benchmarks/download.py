"""Download public coding / general-task benchmarks into benchmarks/data/."""
from __future__ import annotations

import gzip
import json
import shutil
import sys
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(parents=True, exist_ok=True)

HUMANEVAL_URL = "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
GSM8K_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} -> {dest}")
    with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)
    return dest


def download_humaneval() -> Path:
    gz = DATA / "HumanEval.jsonl.gz"
    out = DATA / "HumanEval.jsonl"
    if out.exists() and out.stat().st_size > 1000:
        return out
    _download(HUMANEVAL_URL, gz)
    with gzip.open(gz, "rb") as src, out.open("wb") as dst:
        dst.write(src.read())
    return out


def download_gsm8k(limit: int = 200) -> Path:
    src = DATA / "gsm8k_test.jsonl"
    slim = DATA / "gsm8k_sample.jsonl"
    if slim.exists() and slim.stat().st_size > 100:
        return slim
    try:
        _download(GSM8K_URL, src)
    except Exception as e:
        print(f"GSM8K download skipped: {e}")
        return slim
    n = 0
    with src.open("r", encoding="utf-8") as fin, slim.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            fout.write(line)
            n += 1
            if n >= limit:
                break
    return slim


def download_swebench_verified(limit: int = 50) -> Path:
    """Problem statements only — full Docker eval is not run locally."""
    out = DATA / "swebench_verified_sample.jsonl"
    if out.exists() and out.stat().st_size > 100:
        return out
    rows: list[dict] = []
    api = (
        "https://datasets-server.huggingface.co/rows"
        "?dataset=SWE-bench/SWE-bench_Verified&config=default&split=test"
    )
    try:
        offset = 0
        while len(rows) < limit:
            chunk = min(100, limit - len(rows))
            url = f"{api}&offset={offset}&length={chunk}"
            print(f"Downloading SWE-bench Verified rows {offset}-{offset + chunk}")
            with urllib.request.urlopen(url, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            batch = payload.get("rows") or []
            if not batch:
                break
            for item in batch:
                rec = item.get("row") or {}
                rows.append(
                    {
                        "instance_id": rec.get("instance_id"),
                        "repo": rec.get("repo"),
                        "problem_statement": rec.get("problem_statement"),
                        "hints_text": rec.get("hints_text"),
                    }
                )
            offset += len(batch)
            if len(batch) < chunk:
                break
        with out.open("w", encoding="utf-8") as f:
            for rec in rows[:limit]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return out
    except Exception as e:
        print(f"SWE-bench Verified download skipped: {e}")
        return out


def download_all() -> dict[str, str]:
    paths = {
        "humaneval": str(download_humaneval()),
        "gsm8k": str(download_gsm8k()),
        "swebench_verified": str(download_swebench_verified()),
    }
    print(json.dumps(paths, indent=2))
    return paths


if __name__ == "__main__":
    sys.exit(0 if download_all() else 1)
