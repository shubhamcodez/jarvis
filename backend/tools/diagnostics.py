"""Deterministic compile + test sensors. The model does not get to declare victory."""
from __future__ import annotations

import atexit
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from tools.lsp_client import run_lsp_diagnostics
from tools.overlay_workspace import OverlayWorkspace


def syntax_check_python(workspace: OverlayWorkspace, rel_paths: Optional[list[str]] = None) -> dict[str, Any]:
    paths = rel_paths or [p for p in workspace.changed_paths() if p.endswith(".py")]
    errors: list[dict[str, str]] = []
    checked = 0
    for rel in paths:
        if not rel.endswith(".py"):
            continue
        try:
            text = workspace.raw_text(rel)
        except (OSError, FileNotFoundError, ValueError):
            continue
        checked += 1
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append({"path": rel, "error": str(e)})
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    return {"ok": not errors, "checked": checked, "errors": errors}


def lsp_check_python(workspace: OverlayWorkspace, rel_paths: Optional[list[str]] = None) -> dict[str, Any]:
    """Language-server diagnostics after edits. Falls back to bundled jarvis-pylsp."""
    try:
        return run_lsp_diagnostics(workspace, rel_paths)
    except Exception as e:
        return {"ok": True, "engine": "off", "error": str(e), "diagnostics": [], "checked": 0}


def discover_test_target(root: Path) -> Optional[str]:
    if (root / "tests").is_dir():
        return "tests"
    if (root / "test").is_dir():
        return "test"
    py_tests = list(root.glob("test_*.py")) + list(root.glob("*_test.py"))
    if py_tests:
        return str(py_tests[0].name)
    return None


def _npm_test_cmd(root: Path) -> Optional[list[str]]:
    pkg = root / "package.json"
    if not pkg.is_file() or not shutil.which("npm"):
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict) or "test" not in scripts:
        return None
    return ["npm", "test", "--silent"]


_PYTEST_FUNC = re.compile(r"^def test_\w+", re.M)
_PYTEST_MARK = re.compile(r"^\s*@pytest\.", re.M)
_WORKER_LOCK = threading.Lock()
_WORKER: subprocess.Popen[str] | None = None


def _read_test_head(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:80_000]
    except OSError:
        return ""


def _file_is_stdlib_unittest(path: Path) -> bool:
    """True when the file is unittest.TestCase and does not need pytest."""
    text = _read_test_head(path)
    if not text or "TestCase" not in text:
        return False
    if "import pytest" in text or "from pytest" in text:
        return False
    if _PYTEST_FUNC.search(text) or _PYTEST_MARK.search(text):
        return False
    return True


def _collect_test_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for path in directory.rglob("*.py"):
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            found.append(path)
            if len(found) >= 40:
                break
    return found


def _dir_is_stdlib_unittest(directory: Path) -> bool:
    files = _collect_test_files(directory)
    if not files:
        return False
    # unittest discover's default pattern misses *_test.py. Keep pytest for those.
    if any(not path.name.startswith("test_") for path in files):
        return False
    return all(_file_is_stdlib_unittest(path) for path in files)


def unittest_discover_args(root: Path, path: str = "") -> Optional[tuple[str, str]]:
    """
    (start_dir, pattern) when this tree can be executed with stdlib unittest.

    Pytest-style tests, npm/cargo/go projects, and mixed trees return None so the
    existing command is unchanged.
    """
    explicit = (path or "").strip().replace("\\", "/")
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return None
    if explicit.endswith(".py"):
        file = root / explicit
        if not file.is_file() or not _file_is_stdlib_unittest(file):
            return None
        parent = Path(explicit).parent.as_posix()
        start = "." if parent in ("", ".") else parent
        return start, Path(explicit).name
    if explicit and explicit not in (".", "tests", "test"):
        candidate = root / explicit
        if not candidate.is_dir() or not _dir_is_stdlib_unittest(candidate):
            return None
        return explicit, "test*.py"
    target = explicit or discover_test_target(root) or "tests"
    if target in ("", "."):
        directory = root
        label = "."
    else:
        directory = root / target
        label = target
    if not _dir_is_stdlib_unittest(directory):
        return None
    return label, "test*.py"


def _unittest_command(start: str, pattern: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        start,
        "-p",
        pattern,
        "-t",
        ".",
        "-q",
    ]


def discover_test_command(root: Path, path: str = "") -> list[str]:
    unit = unittest_discover_args(root, path)
    if unit is not None:
        return _unittest_command(unit[0], unit[1])
    explicit = (path or "").strip()
    if explicit.endswith(".py") or (explicit and explicit not in (".", "tests", "test")):
        return [sys.executable, "-m", "pytest", explicit, "-q", "--tb=short"]
    npm = _npm_test_cmd(root)
    if npm and not (root / "pytest.ini").is_file() and not (root / "tests").is_dir() and not list(root.glob("test_*.py")):
        return npm
    if (root / "Cargo.toml").is_file() and shutil.which("cargo") and not (root / "tests").is_dir():
        return ["cargo", "test", "--quiet"]
    if (root / "go.mod").is_file() and shutil.which("go") and not (root / "tests").is_dir():
        return ["go", "test", "./..."]
    target = explicit or discover_test_target(root) or "tests"
    try:
        import pytest  # noqa: F401

        has_pytest = True
    except Exception:
        has_pytest = bool(shutil.which("pytest"))
    if has_pytest:
        return [sys.executable, "-m", "pytest", target, "-q", "--tb=short"]
    start = target if target not in ("", ".") else "."
    return [sys.executable, "-m", "unittest", "discover", "-s", start, "-t", ".", "-q"]


def _unittest_args_from_cmd(cmd: list[str]) -> Optional[tuple[str, str]]:
    if "unittest" not in cmd or "discover" not in cmd:
        return None
    start = "."
    pattern = "test*.py"
    if "-s" in cmd:
        start = cmd[cmd.index("-s") + 1]
    if "-p" in cmd:
        pattern = cmd[cmd.index("-p") + 1]
    return start, pattern


def shutdown_test_worker() -> None:
    """Stop the warm unittest interpreter, if one is running."""
    global _WORKER
    with _WORKER_LOCK:
        proc = _WORKER
        _WORKER = None
    if proc is None:
        return
    try:
        if proc.poll() is None and proc.stdin is not None:
            proc.stdin.write('{"cmd":"stop"}\n')
            proc.stdin.flush()
    except Exception:
        pass
    try:
        proc.wait(timeout=1.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _kill_test_worker() -> None:
    global _WORKER
    proc = _WORKER
    _WORKER = None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def _ensure_test_worker() -> subprocess.Popen[str]:
    global _WORKER
    if _WORKER is not None and _WORKER.poll() is None and _WORKER.stdin and _WORKER.stdout:
        return _WORKER
    from tools.win_subprocess import popen_hidden

    script = Path(__file__).with_name("test_worker.py")
    env = os.environ.copy()
    env["JARVIS_TEST_PARENT_PID"] = str(os.getpid())
    _WORKER = popen_hidden(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=tempfile.gettempdir(),
    )
    return _WORKER


def _readline_timeout(proc: subprocess.Popen[str], timeout: float) -> Optional[str]:
    holder: dict[str, str] = {}

    def _read() -> None:
        try:
            if proc.stdout is not None:
                holder["line"] = proc.stdout.readline()
        except Exception:
            holder["line"] = ""

    thread = threading.Thread(target=_read, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    return holder.get("line", "")


def _worker_request(job: dict[str, str], timeout: float) -> dict[str, Any]:
    """Run one unittest job. Restarts the worker if it dies before the job."""
    payload = json.dumps(job) + "\n"
    with _WORKER_LOCK:
        proc = _ensure_test_worker()
        try:
            assert proc.stdin is not None
            proc.stdin.write(payload)
            proc.stdin.flush()
        except Exception:
            _kill_test_worker()
            proc = _ensure_test_worker()
            assert proc.stdin is not None
            proc.stdin.write(payload)
            proc.stdin.flush()
        deadline = time.monotonic() + timeout
        extra: list[str] = []
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                _kill_test_worker()
                return {"ok": False, "passed": False, "error": "tests timed out", "summary": "tests timed out"}
            line = _readline_timeout(proc, remain)
            if line is None:
                _kill_test_worker()
                return {"ok": False, "passed": False, "error": "tests timed out", "summary": "tests timed out"}
            if line == "":
                _kill_test_worker()
                summary = "\n".join(extra).strip() or "test worker exited"
                return {"ok": False, "passed": False, "returncode": 1, "error": summary, "summary": summary}
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    extra.append(stripped)
                    continue
                if isinstance(data, dict) and "passed" in data:
                    if extra:
                        data["summary"] = ("\n".join(extra) + "\n" + str(data.get("summary") or ""))[-2500:]
                    return data
            extra.append(stripped)


atexit.register(shutdown_test_worker)


def _format_worker_result(cmd: list[str], data: dict[str, Any]) -> dict[str, Any]:
    summary = str(data.get("summary") or data.get("error") or "")
    passed = bool(data.get("passed"))
    out: dict[str, Any] = {
        "ok": passed,
        "passed": passed,
        "returncode": int(data.get("returncode") if data.get("returncode") is not None else (0 if passed else 1)),
        "command": " ".join(cmd),
        "stdout": summary[-6000:],
        "stderr": "",
        "summary": summary[-2500:],
    }
    if data.get("error") and not passed:
        out["error"] = data.get("error")
    return out


def _run_command(dest: Path, cmd: list[str], timeout_sec: float) -> dict[str, Any]:
    from tools.win_subprocess import run_hidden

    proc = run_hidden(
        cmd,
        cwd=str(dest),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5.0, min(float(timeout_sec), 120.0)),
    )
    stdout = (proc.stdout or "")[-6000:]
    stderr = (proc.stderr or "")[-3000:]
    combined = (stdout + "\n" + stderr).strip()
    passed = proc.returncode == 0
    return {
        "ok": passed,
        "passed": passed,
        "returncode": proc.returncode,
        "command": " ".join(cmd),
        "stdout": stdout,
        "stderr": stderr,
        "summary": combined[-2500:],
    }


def run_python_tests(
    workspace: OverlayWorkspace,
    *,
    path: str = "",
    timeout_sec: float = 45.0,
    include_all: bool = True,
) -> dict[str, Any]:
    dest = Path(tempfile.mkdtemp(prefix="ada-swe-test-"))
    try:
        workspace.materialize(dest, include_all=include_all)
        cmd = discover_test_command(dest, path)
        unit = _unittest_args_from_cmd(cmd)
        limit = max(5.0, min(float(timeout_sec), 120.0))
        if unit is not None:
            start, pattern = unit
            try:
                data = _worker_request(
                    {"root": str(dest), "start": start, "pattern": pattern},
                    limit,
                )
            except OSError:
                data = None
            if data is not None:
                return _format_worker_result(cmd, data)
        return _run_command(dest, cmd, limit)
    except subprocess.TimeoutExpired:
        return {"ok": False, "passed": False, "error": "tests timed out"}
    except Exception as e:
        return {"ok": False, "passed": False, "error": str(e)}
    finally:
        shutil.rmtree(dest, ignore_errors=True)
