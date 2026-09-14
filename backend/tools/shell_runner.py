"""
Host shell execution for the shell agent (enabled by default — dangerous).

Shell is **on** unless explicitly turned off with `ADA_ENABLE_SHELL=0` / `false`, or
`ADA_DISABLE_SHELL=1` (JARVIS_* variants still honored). Commands run under a configurable
working directory (default: <repo>/ada-shell-work). On Windows, uses Git Bash if `bash` is on PATH,
otherwise PowerShell. Override with ADA_SHELL=bash|powershell|sh.

This is NOT a security boundary. Anyone who can reach the API can wipe data.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _getenv(*keys: str) -> str:
    for k in keys:
        v = os.environ.get(k)
        if v is not None and str(v).strip() != "":
            return str(v)
    return ""


def is_shell_enabled() -> bool:
    """On in repo/dev; off in packaged installs unless ADA_ENABLE_SHELL=1."""
    d = _getenv("ADA_DISABLE_SHELL", "JARVIS_DISABLE_SHELL").strip().lower()
    if d in ("1", "true", "yes", "on"):
        return False
    v = _getenv("ADA_ENABLE_SHELL", "JARVIS_ENABLE_SHELL").strip().lower()
    if v in ("0", "false", "no", "off", "disabled"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    try:
        from config import is_packaged

        if is_packaged():
            return False
    except Exception:
        if os.environ.get("ADA_PACKAGED", "").strip().lower() in ("1", "true", "yes", "on"):
            return False
    return True


def _repo_root() -> Path:
    # backend/tools/shell_runner.py -> parents[2] = backend, [3] = repo — wait:
    # shell_runner is at backend/tools/shell_runner.py -> parent=tools, parent.parent=backend, parent.parent.parent=repo root
    return Path(__file__).resolve().parent.parent.parent


def get_shell_workdir() -> Path:
    raw = _getenv("ADA_SHELL_WORKDIR", "JARVIS_SHELL_WORKDIR").strip()
    if raw:
        p = Path(raw).expanduser()
    else:
        root = _repo_root()
        ada_p = root / "ada-shell-work"
        leg_p = root / "jarvis-shell-work"
        p = ada_p if ada_p.exists() or not leg_p.exists() else leg_p
    return p.resolve()


def ensure_shell_workdir() -> Path:
    wd = get_shell_workdir()
    wd.mkdir(parents=True, exist_ok=True)
    return wd


def shell_runtime_label() -> str:
    """Short description for LLM prompts."""
    mode = _resolve_shell_mode()
    wd = get_shell_workdir()
    plat = "Windows" if sys.platform == "win32" else "Unix"
    return f"{plat}, backend={mode}, cwd={wd}"


def _resolve_shell_mode() -> str:
    v = _getenv("ADA_SHELL", "JARVIS_SHELL").strip().lower()
    if v in ("powershell", "pwsh", "bash", "sh"):
        return "powershell" if v == "pwsh" else v
    if sys.platform == "win32":
        if shutil.which("bash"):
            return "bash"
        return "powershell"
    if shutil.which("bash"):
        return "bash"
    return "sh"


def _clip(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n…[truncated]…"


# Minimal guardrails — not sufficient for untrusted users.
_BLOCKED_PATTERNS = [
    re.compile(r"rm\s+(-[rfFR]+\s+)+/(?:\s|$)", re.I),  # rm -rf / ...
    re.compile(r"rm\s+(-[rfFR]+\s+)+\*", re.I),
    re.compile(r":\s*\(\)\s*\{", re.I),  # fork bomb
    re.compile(r"\bdd\b.*\bif\s*=\s*/dev/", re.I),
    re.compile(r"\bmkfs\.", re.I),
    re.compile(r"\\\.\\", re.I),  # Windows device paths
    re.compile(r"\bformat\.?\s+[a-z]\s*:", re.I),
    re.compile(r"Invoke-WebRequest.*-OutFile", re.I),  # often exfil; optional — user may want wget
]
# Too aggressive to block IWR globally; drop that pattern.

_BLOCKED_SUBSTRINGS = [
    "rm -rf / ",
    "rm -rf /*",
    "rm -fr /",
    "rm -rf /\t",
    "del /f /s /q c:\\",
    "remove-item -recurse -force c:\\windows",
]


def why_command_blocked(command: str) -> str | None:
    """Return a short reason if the command is refused, else None."""
    cmd = (command or "").strip()
    if not cmd:
        return "empty command"
    if len(cmd) > 8000:
        return "command too long"
    low = cmd.lower()
    for sub in _BLOCKED_SUBSTRINGS:
        if sub in low:
            return f"blocked pattern ({sub[:40]!r})"
    for rx in _BLOCKED_PATTERNS:
        if rx.search(cmd):
            return "blocked pattern (high-risk shell construct)"
    return None


def run_shell_command(command: str, timeout_sec: float | None = None) -> dict:
    """
    Run one command in the configured shell under ADA_SHELL_WORKDIR.
    Returns dict: ok, returncode, stdout, stderr, error (optional), shell (mode).
    """
    if not is_shell_enabled():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": "Shell tool disabled (ADA_ENABLE_SHELL=0 or ADA_DISABLE_SHELL=1).",
            "shell": None,
        }

    reason = why_command_blocked(command)
    if reason:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"Command blocked: {reason}",
            "shell": _resolve_shell_mode(),
        }

    cwd = ensure_shell_workdir()
    mode = _resolve_shell_mode()
    t = timeout_sec if timeout_sec is not None else float(
        _getenv("ADA_SHELL_TIMEOUT", "JARVIS_SHELL_TIMEOUT") or "120"
    )
    max_out = int(_getenv("ADA_SHELL_MAX_OUTPUT", "JARVIS_SHELL_MAX_OUTPUT") or "32000")
    env = os.environ.copy()

    try:
        if mode == "powershell":
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=t,
                env=env,
            )
        elif mode == "bash":
            bash = shutil.which("bash") or "bash"
            proc = subprocess.run(
                [bash, "-lc", command],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=t,
                env=env,
            )
        else:
            exe = shutil.which("sh") or "/bin/sh"
            proc = subprocess.run(
                [exe, "-c", command],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=t,
                env=env,
            )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": _clip(getattr(e, "stdout", None) or "", max_out),
            "stderr": _clip((getattr(e, "stderr", None) or "") + "\n[timeout]", max_out),
            "error": f"timeout after {t}s",
            "shell": mode,
        }
    except FileNotFoundError as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"shell not found: {e}",
            "shell": mode,
        }
    except Exception as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
            "shell": mode,
        }

    out = _clip(proc.stdout or "", max_out)
    err = _clip(proc.stderr or "", max_out)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": out,
        "stderr": err,
        "shell": mode,
    }
