"""Packaged FastAPI entrypoint (PyInstaller sidecar). Binds localhost only."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _platform_data_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Jarvis"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Jarvis"
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    return Path(xdg) / "Jarvis" if xdg else Path.home() / ".local" / "share" / "Jarvis"


def _prepare_env() -> None:
    frozen = getattr(sys, "frozen", False)
    if frozen:
        os.environ.setdefault("ADA_PACKAGED", "1")
    os.environ.setdefault("UVICORN_RELOAD", "0")
    if frozen:
        here = Path(sys.executable).resolve().parent
    else:
        here = Path(__file__).resolve().parent
    os.chdir(str(here) if (here / "main.py").exists() else str(Path(__file__).resolve().parent))
    os.environ.setdefault("ADA_DATA_DIR", str(_platform_data_dir()))


def main() -> None:
    _prepare_env()
    import uvicorn

    raw_port = os.environ.get("PORT", "8000")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = 8000
    if port < 1 or port > 65535:
        port = 8000
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
