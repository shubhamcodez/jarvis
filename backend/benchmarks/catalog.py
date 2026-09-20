"""Runnable local suites plus metadata for downloaded public benchmarks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
DATA = ROOT / "data"

SWE_FIXTURES: list[dict[str, Any]] = [
    {
        "id": "mean_bias",
        "kind": "swe",
        "goal": (
            "mean() in pkg/stats.py is wrong: it divides by n-1 (sample variance style) "
            "instead of n. Fix it so the arithmetic mean of [2,4,6] is 4 and mean([1]) is 1. "
            "Do not change the tests."
        ),
        "dir": "mean_bias",
    },
    {
        "id": "off_by_one",
        "kind": "swe",
        "goal": (
            "count_to(n) should return [0, 1, ..., n-1]. It currently skips 0. "
            "Fix pkg/counter.py so tests/test_counter.py pass. Do not change the tests."
        ),
        "dir": "off_by_one",
    },
    {
        "id": "json_last_key",
        "kind": "swe",
        "goal": (
            "parse_pairs in pkg/pairs.py drops the final comma-separated entry. "
            "A single item such as only=yes must also be kept. Fix the loop so every "
            "entry is parsed. Do not change the tests."
        ),
        "dir": "json_last_key",
    },
    {
        "id": "slugify",
        "kind": "swe",
        "goal": (
            "slugify() must: lowercase, collapse whitespace to single hyphens, strip "
            "leading/trailing hyphens, return '' for empty/whitespace-only input, and "
            "strip characters that are not alphanumeric or hyphen. Fix pkg/text.py. "
            "Do not change the tests."
        ),
        "dir": "slugify",
    },
    {
        "id": "multi_file_tax",
        "kind": "swe",
        "goal": (
            "checkout.total(amount, state) should apply the lookup table in pkg/rates.py. "
            "tax_rate() in pkg/tax.py currently always returns 0 — it must read RATES "
            "and default unknown regions to 0. Example: CA on 100 -> 107.25. "
            "Do not change the tests."
        ),
        "dir": "multi_file_tax",
    },
]

GENERAL_CASES: list[dict[str, Any]] = [
    {
        "id": "general_factorial",
        "kind": "sandbox",
        "goal": "Write Python to print factorial of 10. Use the sandbox.",
        "expect_contains": ["3628800"],
    },
    {
        "id": "general_tip",
        "kind": "sandbox",
        "goal": "Compute a 15% tip on $80 and print the tip amount only as a number.",
        "expect_contains": ["12"],
    },
    {
        "id": "general_json",
        "kind": "sandbox",
        "goal": (
            "Given data = {'items':[1,2,3,4]}, print the sum of items as a single integer."
        ),
        "expect_contains": ["10"],
    },
    {
        "id": "general_emails",
        "kind": "sandbox",
        "goal": (
            "Extract email addresses from 'Reach a@x.com or b@y.org thanks' and print them "
            "sorted, comma-separated, no spaces."
        ),
        "expect_contains": ["a@x.com,b@y.org"],
    },
]


def swe_fixture_path(item: dict[str, Any]) -> Path:
    return FIXTURES / str(item["dir"])
