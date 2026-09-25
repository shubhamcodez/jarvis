"""Warm stdlib-unittest runner.

The parent process keeps one hidden interpreter alive and sends one JSON job per
line. User modules are dropped after every job so the next tree cannot reuse a
stale import. Protocol stdout is reserved for JSON results.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import traceback
import unittest

sys.dont_write_bytecode = True


def _base_sys_path() -> list[str]:
    script_dir = os.path.abspath(os.path.dirname(__file__))
    kept: list[str] = []
    for entry in sys.path:
        if not entry:
            continue
        if os.path.abspath(entry) == script_dir:
            continue
        kept.append(entry)
    return kept


_BASE_MODULES = set(sys.modules)
_BASE_PATH = _base_sys_path()


def _reset_imports() -> None:
    for name in list(sys.modules):
        if name not in _BASE_MODULES:
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _parent_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def _watch_parent(pid: int) -> None:
    import time

    while True:
        time.sleep(0.5)
        if not _parent_alive(pid):
            os._exit(0)


def _suite_from_files(loader: unittest.TestLoader, start_path: str, pattern: str) -> unittest.TestSuite:
    """Load test_*.py modules by path. Works when tests/ is not a package."""
    suite = unittest.TestSuite()
    if not os.path.isdir(start_path):
        return suite
    for dirpath, dirnames, filenames in os.walk(start_path):
        dirnames[:] = [name for name in dirnames if name != "__pycache__" and not name.startswith(".")]
        for name in filenames:
            if not fnmatch.fnmatch(name, pattern):
                continue
            full = os.path.join(dirpath, name)
            mod_name = "jarvis_under_test_" + hashlib.sha256(full.encode("utf-8", errors="replace")).hexdigest()[:16]
            spec = importlib.util.spec_from_file_location(mod_name, full)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(mod_name, None)
                raise
            suite.addTests(loader.loadTestsFromModule(module))
    return suite


def _load_suite(loader: unittest.TestLoader, root_abs: str, start_dir: str, pattern: str) -> unittest.TestSuite:
    start_path = root_abs if start_dir in ("", ".") else os.path.join(root_abs, start_dir)
    packaged = start_dir in ("", ".") or os.path.isfile(os.path.join(start_path, "__init__.py"))
    if packaged:
        try:
            return loader.discover(start_dir or ".", pattern=pattern, top_level_dir=root_abs)
        except ImportError:
            pass
    return _suite_from_files(loader, start_path, pattern)


def _run(root: str, start: str, pattern: str) -> dict:
    import gc

    root_abs = os.path.abspath(root)
    start_dir = start if start not in ("", ".") else "."
    pattern = pattern or "test*.py"
    try:
        if not os.path.isdir(root_abs):
            return {
                "ok": False,
                "passed": False,
                "returncode": 1,
                "summary": f"workspace missing: {root_abs}",
                "tests": 0,
            }
        _reset_imports()
        os.chdir(root_abs)
        sys.path[:] = [root_abs, *[p for p in _BASE_PATH if os.path.abspath(p) != root_abs]]
        capture = io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = capture
        sys.stderr = capture
        try:
            loader = unittest.TestLoader()
            suite = _load_suite(loader, root_abs, start_dir, pattern)
            result = unittest.TextTestRunner(stream=capture, verbosity=1, buffer=True).run(suite)
        finally:
            sys.stdout = real_out
            sys.stderr = real_err
        ran = int(result.testsRun or 0)
        text = capture.getvalue().strip()
        ok = bool(result.wasSuccessful()) and ran > 0
        if ran == 0:
            ok = False
            text = (text + "\nNo tests ran.").strip()
        text += f"\nRan {ran} tests; failures={len(result.failures)} errors={len(result.errors)}"
        return {
            "ok": ok,
            "passed": ok,
            "returncode": 0 if ok else 1,
            "summary": text[-2500:],
            "tests": ran,
        }
    except Exception:
        return {
            "ok": False,
            "passed": False,
            "returncode": 1,
            "summary": traceback.format_exc()[-2500:],
            "tests": 0,
        }
    finally:
        _reset_imports()
        gc.collect()
        try:
            os.chdir(tempfile.gettempdir())
        except OSError:
            pass


def main() -> None:
    raw_pid = (os.environ.get("JARVIS_TEST_PARENT_PID") or "").strip()
    if raw_pid.isdigit():
        import threading

        threading.Thread(target=_watch_parent, args=(int(raw_pid),), daemon=True).start()
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            job = json.loads(text)
        except json.JSONDecodeError:
            job = {"cmd": "bad"}
        if not isinstance(job, dict) or job.get("cmd") == "stop":
            break
        if job.get("cmd") == "bad":
            out = {"ok": False, "passed": False, "returncode": 1, "summary": "bad job", "tests": 0}
        else:
            out = _run(str(job.get("root") or ""), str(job.get("start") or "tests"), str(job.get("pattern") or "test*.py"))
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
