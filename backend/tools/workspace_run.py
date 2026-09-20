"""Run a file under the linked workspace (VS Code–style Run, not the host shell)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_OUTPUT = 32_000
_DEFAULT_TIMEOUT = 120.0
_MAX_TIMEOUT = 300.0

_BLOCKED_EXTS = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".com",
        ".scr",
        ".msi",
        ".sys",
        ".bin",
        ".env",
    }
)

_KEEP_ENV = {
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
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "NODE_PATH",
}


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _python_argv(path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        py = _which("python", "python3", "py")
        if not py:
            raise FileNotFoundError("Python is not on PATH. Install Python to run .py files.")
        if Path(py).name.lower() in ("py.exe", "py"):
            return [py, "-3", str(path)]
        return [py, str(path)]
    return [sys.executable, str(path)]


def command_for_path(path: Path) -> dict[str, Any]:
    """Map a file to argv + runtime label. Does not execute."""
    ext = path.suffix.lower()
    if ext in _BLOCKED_EXTS or path.name.lower() in {".env", ".env.local"}:
        raise ValueError(f"Refusing to run {path.name} (blocked file type).")

    if ext in {".py", ".pyw"}:
        argv = _python_argv(path)
        return {"argv": argv, "runtime": "Python"}
    if ext in {".js", ".mjs", ".cjs"}:
        node = _which("node")
        if not node:
            raise FileNotFoundError("Node.js is not on PATH. Install Node to run JavaScript files.")
        return {"argv": [node, str(path)], "runtime": "Node"}
    if ext in {".ts", ".mts", ".cts"}:
        tsx = _which("tsx")
        if tsx:
            return {"argv": [tsx, str(path)], "runtime": "TypeScript"}
        node = _which("node")
        if node:
            return {"argv": [node, "--experimental-strip-types", str(path)], "runtime": "TypeScript"}
        raise FileNotFoundError("TypeScript runner not found. Install Node.js (22+) or the tsx CLI.")
    if ext == ".ps1":
        exe = _which("pwsh", "powershell")
        if not exe:
            raise FileNotFoundError("PowerShell is not on PATH.")
        return {
            "argv": [exe, "-NoProfile", "-NonInteractive", "-File", str(path)],
            "runtime": "PowerShell",
        }
    if ext in {".sh", ".bash"}:
        bash = _which("bash")
        if not bash:
            raise FileNotFoundError("bash is not on PATH.")
        return {"argv": [bash, str(path)], "runtime": "Bash"}
    if ext == ".rb":
        ruby = _which("ruby")
        if not ruby:
            raise FileNotFoundError("Ruby is not on PATH.")
        return {"argv": [ruby, str(path)], "runtime": "Ruby"}
    if ext == ".go":
        go = _which("go")
        if not go:
            raise FileNotFoundError("Go is not on PATH.")
        return {"argv": [go, "run", str(path)], "runtime": "Go"}
    if ext == ".php":
        php = _which("php")
        if not php:
            raise FileNotFoundError("PHP is not on PATH.")
        return {"argv": [php, str(path)], "runtime": "PHP"}
    if ext == ".pl":
        perl = _which("perl")
        if not perl:
            raise FileNotFoundError("Perl is not on PATH.")
        return {"argv": [perl, str(path)], "runtime": "Perl"}
    if ext == ".lua":
        lua = _which("lua", "luajit")
        if not lua:
            raise FileNotFoundError("Lua is not on PATH.")
        return {"argv": [lua, str(path)], "runtime": "Lua"}
    if ext in {".r", ".R"}:
        rscript = _which("Rscript")
        if not rscript:
            raise FileNotFoundError("Rscript is not on PATH.")
        return {"argv": [rscript, str(path)], "runtime": "R"}
    if ext in {".bat", ".cmd"}:
        comspec = os.environ.get("COMSPEC") or _which("cmd") or "cmd.exe"
        return {"argv": [comspec, "/c", str(path)], "runtime": "Batch"}

    raise ValueError(
        f"No Run command for {ext or 'this'} files. Open a Python, JavaScript, PowerShell, or similar script."
    )


def _clip(text: str | None) -> str:
    s = text or ""
    if len(s) <= _MAX_OUTPUT:
        return s
    return s[:_MAX_OUTPUT] + f"\n… truncated ({len(s)} chars)"


def _child_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if ku in _KEEP_ENV or ku.startswith("PROCESSOR_") or ku.startswith("LC_") or ku.startswith("PROGRAMFILES"):
            env[k] = v
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _display_command(argv: list[str], rel_path: str) -> str:
    pretty: list[str] = []
    rel = rel_path.replace("\\", "/")
    base = Path(rel).name
    for a in argv:
        p = Path(a)
        if p.is_absolute() and p.name == base:
            pretty.append(rel)
        else:
            pretty.append(a)
    return " ".join(pretty)


def _kill_proc(proc: subprocess.Popen[str]) -> None:
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def run_workspace_file(rel_path: str, timeout_sec: float | None = None) -> dict[str, Any]:
    """Execute one workspace file under the linked root. Does not require host shell."""
    from tools.workspace_io import get_workspace_root, resolve_under_root

    try:
        raw = get_workspace_root()
        if not raw:
            raise ValueError("No workspace folder is linked.")
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace path is not a directory: {root}")
        target = resolve_under_root(rel_path, root)
    except ValueError as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
            "runtime": None,
            "display": "",
        }

    if not target.is_file():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"File not found: {rel_path}",
            "runtime": None,
            "display": "",
        }

    try:
        spec = command_for_path(target)
    except (ValueError, FileNotFoundError) as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
            "runtime": None,
            "display": "",
        }

    argv = spec["argv"]
    runtime = spec["runtime"]
    display = _display_command(argv, rel_path)
    t = _DEFAULT_TIMEOUT if timeout_sec is None else float(timeout_sec)
    t = max(1.0, min(_MAX_TIMEOUT, t))

    try:
        from tools.win_subprocess import popen_hidden

        proc = popen_hidden(
            argv,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_child_env(),
        )
    except FileNotFoundError as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"Interpreter not found: {e}",
            "runtime": runtime,
            "display": display,
        }
    except Exception as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
            "runtime": runtime,
            "display": display,
        }

    stdout = ""
    stderr = ""
    try:
        stdout, stderr = proc.communicate(timeout=t)
    except subprocess.TimeoutExpired:
        _kill_proc(proc)
        try:
            leftover_out, leftover_err = proc.communicate(timeout=2)
        except Exception:
            leftover_out, leftover_err = "", ""
        return {
            "ok": False,
            "returncode": -1,
            "stdout": _clip(leftover_out),
            "stderr": _clip(leftover_err),
            "error": f"Timed out after {int(t)}s.",
            "runtime": runtime,
            "display": display,
        }

    code = int(proc.returncode if proc.returncode is not None else -1)
    return {
        "ok": code == 0,
        "returncode": code,
        "stdout": _clip(stdout),
        "stderr": _clip(stderr),
        "error": None if code == 0 else (stderr.strip() or f"Exited with code {code}."),
        "runtime": runtime,
        "display": display,
        "elapsed_ms": None,
    }
