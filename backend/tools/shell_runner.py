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
import time
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
        return v
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
    re.compile(r"Invoke-WebRequest.*-OutFile", re.I),
    re.compile(r"\bcurl\b.*\|\s*(iex|invoke-expression)", re.I),
    re.compile(r"iwr\s+.+\|\s*iex", re.I),
    re.compile(r"-enc(?:odedcommand)?\s+", re.I),
    re.compile(r"\bstop-computer\b", re.I),
    re.compile(r"\bshutdown\b", re.I),
    re.compile(r"rd\s+/s", re.I),
]

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


def _kill_proc(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def run_shell_command(
    command: str,
    timeout_sec: float | None = None,
    *,
    run_id: str | None = None,
) -> dict:
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
    _KEEP = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "LANG",
        "LC_ALL",
        "TERM",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "SYSTEMDRIVE",
        "PROGRAMFILES",
        "PROGRAMDATA",
        "LOCALAPPDATA",
        "APPDATA",
    }
    env = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if ku in _KEEP or ku.startswith("PROCESSOR_") or ku.startswith("LC_") or ku.startswith("PROGRAMFILES"):
            env[k] = v

    if mode in ("powershell", "pwsh"):
        exe = shutil.which("pwsh" if mode == "pwsh" else "powershell") or (
            "pwsh" if mode == "pwsh" else "powershell"
        )
        argv = [exe, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", command]
    elif mode == "bash":
        bash = shutil.which("bash") or "bash"
        argv = [bash, "-lc", command]
    else:
        exe = shutil.which("sh") or "/bin/sh"
        argv = [exe, "-c", command]

    try:
        from tools.win_subprocess import popen_hidden

        proc = popen_hidden(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
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

    deadline = time.monotonic() + max(1.0, float(t))
    stdout = ""
    stderr = ""
    try:
        while proc.poll() is None:
            if run_id:
                try:
                    from agents.run_control import is_cancelled

                    if is_cancelled(run_id):
                        _kill_proc(proc)
                        return {
                            "ok": False,
                            "returncode": -1,
                            "stdout": "",
                            "stderr": "",
                            "error": "Stopped by user.",
                            "shell": mode,
                        }
                except Exception:
                    pass
            if time.monotonic() >= deadline:
                _kill_proc(proc)
                return {
                    "ok": False,
                    "returncode": -1,
                    "stdout": "",
                    "stderr": "[timeout]",
                    "error": f"timeout after {t}s",
                    "shell": mode,
                }
            time.sleep(0.2)
        stdout, stderr = proc.communicate(timeout=2)
    except Exception as e:
        _kill_proc(proc)
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
            "shell": mode,
        }

    out = _clip(stdout or "", max_out)
    err = _clip(stderr or "", max_out)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": out,
        "stderr": err,
        "shell": mode,
    }
