"""Paths for observability data (traces, evals, optimization)."""
from pathlib import Path


def _root() -> Path:
    try:
        from config import data_root

        return data_root()
    except Exception:
        return Path(__file__).resolve().parent.parent.parent


_ROOT = _root()
_ADA_OBS = _ROOT / "ada-observability"
_JARVIS_OBS = _ROOT / "jarvis-observability"
if _ADA_OBS.exists():
    OBS_DIR = _ADA_OBS
elif _JARVIS_OBS.exists():
    OBS_DIR = _JARVIS_OBS
else:
    OBS_DIR = _ADA_OBS
TRACES_DIR = OBS_DIR / "traces"
EVALS_DIR = OBS_DIR / "evals"
OPT_DIR = OBS_DIR / "optimization"


def ensure_dirs():
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    OPT_DIR.mkdir(parents=True, exist_ok=True)
