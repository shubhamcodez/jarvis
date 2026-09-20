"""Cheap deterministic localization — Agentless-style, no extra LLM call."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from tools.file_grep import grep_files
from tools.git_history import recent_touched_files
from tools.overlay_workspace import OverlayWorkspace

_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "when",
        "should",
        "would",
        "could",
        "file",
        "code",
        "function",
        "please",
        "fix",
        "bug",
        "issue",
        "error",
        "test",
        "tests",
        "add",
        "update",
        "change",
        "implement",
    }
)


def extract_needles(goal: str, limit: int = 8) -> list[str]:
    text = goal or ""
    idents = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    paths = re.findall(r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|md)", text)
    scored: list[str] = []
    for p in paths:
        scored.append(p.replace("\\", "/"))
    seen = set(scored)
    for tok in idents:
        low = tok.lower()
        if low in _STOP or low in seen:
            continue
        seen.add(low)
        scored.append(tok)
        if len(scored) >= limit:
            break
    return scored[:limit]


def localize(workspace: OverlayWorkspace, goal: str, *, max_files: int = 8) -> dict[str, Any]:
    needles = extract_needles(goal)
    hits: Counter[str] = Counter()
    snippets: dict[str, list[str]] = {}
    for needle in needles:
        result = grep_files(workspace.root, needle, max_results=40, fixed_string=True, ignore_case=True)
        if not result.get("ok"):
            continue
        for m in result.get("matches") or []:
            abs_path = m.get("path") or ""
            try:
                rel = str(workspace.root.joinpath(".").joinpath(abs_path))
                rel_p = str(workspace.root)
                if abs_path.startswith(rel_p):
                    rel = abs_path[len(rel_p) :].lstrip("\\/").replace("\\", "/")
                else:
                    rel = abs_path.replace("\\", "/")
            except Exception:
                rel = abs_path.replace("\\", "/")
            if not rel or rel.endswith((".md", ".txt", ".rst")) and "test" not in rel:
                if rel.endswith((".md", ".rst")):
                    continue
            hits[rel] += 1
            snippets.setdefault(rel, [])
            if len(snippets[rel]) < 3:
                snippets[rel].append(f"{m.get('line')}: {(m.get('snippet') or '')[:160]}")
    for rel in recent_touched_files(workspace.root, limit=6):
        if rel.endswith((".md", ".rst", ".txt")):
            continue
        hits[rel] += 1
    ranked = [p for p, _ in hits.most_common(max_files)]
    if not ranked:
        ranked = [p for p in workspace.list_rel_paths(max_files=40) if p.endswith(".py")][:max_files]
    return {
        "ok": True,
        "needles": needles,
        "files": ranked,
        "snippets": {k: snippets.get(k, []) for k in ranked},
    }
