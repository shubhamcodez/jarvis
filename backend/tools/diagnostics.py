"""Deterministic compile + test sensors. The model does not get to declare victory."""
from __future__ import annotations

import json
import py_compile
import shutil
import subprocess
import sys
import tempfile
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


def discover_test_command(root: Path, path: str = "") -> list[str]:
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
    return [sys.executable, "-m", "unittest", "discover", "-s", start, "-q"]


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
        proc = subprocess.run(
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
    except subprocess.TimeoutExpired:
        return {"ok": False, "passed": False, "error": "tests timed out"}
    except Exception as e:
        return {"ok": False, "passed": False, "error": str(e)}
    finally:
        shutil.rmtree(dest, ignore_errors=True)
