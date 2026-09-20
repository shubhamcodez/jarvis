"""Hide Windows console windows when spawning child processes."""
from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_popen_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": flags, "startupinfo": startup}


def run_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    kwargs.update(hidden_popen_kwargs())
    return subprocess.run(*args, **kwargs)


def popen_hidden(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    kwargs.update(hidden_popen_kwargs())
    return subprocess.Popen(*args, **kwargs)
