"""
Sandboxed Python execution for model/tool use: child process, timeout, restricted builtins.

Security: best-effort only; do not expose to untrusted humans without extra isolation (VM, container).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

WORKER = Path(__file__).resolve().parent / "sandbox_worker.py"
MAX_CODE_BYTES = 100_000
DEFAULT_TIMEOUT_SEC = 15.0
MAX_TIMEOUT_SEC = 60.0

_PYTHON_FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# User must show intent to execute; avoids running random pasted code.
_RUN_TRIGGERS = frozenset(
    {
        "run this",
        "run the",
        "execute this",
        "execute the",
        "run python",
        "python sandbox",
        "in the sandbox",
        "evaluate this",
        "compute with python",
        "calculate with python",
    }
)


def run_sandboxed_python(code: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """
    Execute Python in a subprocess with restricted globals (see sandbox_worker.py).

    Returns a dict with ok, stdout, stderr, and optional error/traceback.
    """
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "no code provided"}
    encoded = code.encode("utf-8")
    if len(encoded) > MAX_CODE_BYTES:
        return {"ok": False, "error": f"code exceeds {MAX_CODE_BYTES} bytes"}

    try:
        t = float(timeout_sec)
    except (TypeError, ValueError):
        t = DEFAULT_TIMEOUT_SEC
    t = max(1.0, min(t, MAX_TIMEOUT_SEC))

    if not WORKER.is_file():
        return {"ok": False, "error": "sandbox_worker.py missing"}

    scratch = tempfile.mkdtemp(prefix="ada-sandbox-")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "TEMP": scratch,
        "TMP": scratch,
        "LANG": os.environ.get("LANG", "C"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "MPLBACKEND": "Agg",
        "PYTHONNOUSERSITE": "1",
        "ADA_SANDBOX": "1",
    }
    env = {k: v for k, v in env.items() if v}

    try:
        proc = subprocess.run(
            [sys.executable, str(WORKER)],
            input=json.dumps({"code": code}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=t,
            env=env,
            cwd=scratch,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {t}s", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"ok": False, "error": f"subprocess failed: {type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    raw_out = (proc.stdout or "").strip()
    if not raw_out:
        err = (proc.stderr or "").strip()
        return {
            "ok": False,
            "error": "sandbox produced no output",
            "stderr": err,
            "returncode": proc.returncode,
        }
    try:
        result = json.loads(raw_out.splitlines()[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid sandbox JSON output",
            "raw_stdout": raw_out[:2000],
            "stderr": (proc.stderr or "")[:2000],
        }
    if proc.returncode != 0 and not result.get("ok"):
        result.setdefault("returncode", proc.returncode)
    return result


def extract_python_fences(text: str) -> list[str]:
    return [m.strip() for m in _PYTHON_FENCE.findall(text or "") if m.strip()]


def _wants_run(message_lower: str) -> bool:
    if any(tr in message_lower for tr in _RUN_TRIGGERS):
        return True
    if "python" in message_lower and (
        "run " in message_lower or "execute" in message_lower or "evaluate" in message_lower
    ):
        return True
    return False


def try_python_sandbox_tool(message: str) -> Optional[tuple[str, dict[str, Any]]]:
    """
    If the user clearly asked to run Python and included a ```python fence, execute in sandbox.

    Returns (system_block, tool_used) or None.
    """
    text = message or ""
    if not text.strip():
        return None
    lower = text.lower()
    blocks = extract_python_fences(text)
    if not blocks:
        return None
    if not _wants_run(lower):
        return None

    code = "\n\n".join(blocks)
    result = run_sandboxed_python(code)
    summary = json.dumps(result, ensure_ascii=False, indent=2)[:8000]
    if result.get("ok"):
        block = (
            "SANDBOXED PYTHON RESULT (use this in your answer; stdout is the program output):\n"
            f"{summary}"
        )
    else:
        block = (
            "SANDBOXED PYTHON RUN FAILED (explain briefly to the user):\n"
            f"{summary}"
        )
    tool_used = {
        "name": "python_sandbox",
        "input": code[:2000] + ("…" if len(code) > 2000 else ""),
        "result": summary[:8000],
    }
    return block, tool_used
