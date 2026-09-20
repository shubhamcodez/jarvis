"""Fetch a GitHub issue so 'implement #64' does not require copy-paste."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Optional

_ISSUE_REF = re.compile(
    r"(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#|(?:issue|issues/)?)(?P<num>\d+)",
    re.IGNORECASE,
)


def extract_issue_ref(text: str) -> Optional[dict[str, str]]:
    m = _ISSUE_REF.search(text or "")
    if not m:
        return None
    return {"repo": (m.group("repo") or "").strip(), "number": m.group("num")}


def fetch_issue(text: str, *, timeout_sec: float = 20.0) -> dict[str, Any]:
    ref = extract_issue_ref(text)
    if not ref:
        return {"ok": False, "error": "no issue number found"}
    gh = shutil.which("gh")
    if not gh:
        return {"ok": False, "error": "GitHub CLI (gh) is not installed.", "ref": ref}
    cmd = [gh, "issue", "view", ref["number"], "--json", "title,body,labels,url,state"]
    if ref["repo"]:
        cmd.extend(["--repo", ref["repo"]])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e), "ref": ref}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "gh failed")[:500], "ref": ref}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "gh returned non-JSON", "ref": ref}
    labels = [x.get("name") for x in (data.get("labels") or []) if isinstance(x, dict)]
    return {
        "ok": True,
        "ref": ref,
        "title": data.get("title") or "",
        "body": (data.get("body") or "")[:6000],
        "url": data.get("url") or "",
        "state": data.get("state") or "",
        "labels": labels,
    }


def format_issue_for_prompt(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return ""
    labels = ", ".join(result.get("labels") or []) or "none"
    return (
        "GITHUB ISSUE (authoritative task text):\n"
        f"Title: {result.get('title')}\n"
        f"URL: {result.get('url')}\n"
        f"State: {result.get('state')}  Labels: {labels}\n\n"
        f"{result.get('body')}"
    )
