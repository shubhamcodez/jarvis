"""Packaged FastAPI entrypoint (PyInstaller sidecar). Binds localhost only."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_env() -> None:
    os.environ.setdefault("ADA_PACKAGED", "1")
    os.environ.setdefault("UVICORN_RELOAD", "0")
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
    else:
        here = Path(__file__).resolve().parent
    os.chdir(str(here) if (here / "main.py").exists() else str(Path(__file__).resolve().parent))
    # Writable user data
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    os.environ.setdefault("ADA_DATA_DIR", str(Path(appdata) / "Ada"))


def main() -> None:
    _prepare_env()
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
