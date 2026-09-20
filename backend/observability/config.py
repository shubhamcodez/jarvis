"""Paths for observability data (traces, evals, optimization).

Writes always go to jarvis-observability. The leftover ada-observability
folder is read as a fallback and copied once if Jarvis has no files yet.
"""
from __future__ import annotations

from pathlib import Path


def _root() -> Path:
    try:
        from config import data_root

        return data_root()
    except Exception:
        return Path(__file__).resolve().parent.parent.parent


def obs_dir() -> Path:
    return _root() / "jarvis-observability"


def legacy_obs_dir() -> Path:
    return _root() / "ada-observability"


def _bind() -> None:
    global OBS_DIR, TRACES_DIR, EVALS_DIR, OPT_DIR
    OBS_DIR = obs_dir()
    TRACES_DIR = OBS_DIR / "traces"
    EVALS_DIR = OBS_DIR / "evals"
    OPT_DIR = OBS_DIR / "optimization"


OBS_DIR = obs_dir()
TRACES_DIR = OBS_DIR / "traces"
EVALS_DIR = OBS_DIR / "evals"
OPT_DIR = OBS_DIR / "optimization"


def _copy_if_dest_empty(src: Path, dest: Path) -> None:
    if not src.exists() or src.stat().st_size == 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.write_bytes(src.read_bytes())


def _migrate_legacy() -> None:
    src = legacy_obs_dir()
    dst = OBS_DIR
    try:
        if not src.is_dir() or src.resolve() == dst.resolve():
            return
    except OSError:
        return
    pairs = (
        (src / "traces" / "trace.jsonl", TRACES_DIR / "trace.jsonl"),
        (src / "traces" / "spans.jsonl", TRACES_DIR / "spans.jsonl"),
        (src / "traces" / "actions.jsonl", TRACES_DIR / "actions.jsonl"),
        (src / "logs" / "app.jsonl", OBS_DIR / "logs" / "app.jsonl"),
        (src / "evals" / "eval_cases.jsonl", EVALS_DIR / "eval_cases.jsonl"),
        (src / "evals" / "eval_runs.jsonl", EVALS_DIR / "eval_runs.jsonl"),
        (src / "optimization" / "metrics.json", OPT_DIR / "metrics.json"),
        (src / "optimization" / "optimization_stats.json", OPT_DIR / "optimization_stats.json"),
    )
    for a, b in pairs:
        try:
            _copy_if_dest_empty(a, b)
        except OSError:
            continue


def ensure_dirs() -> None:
    _bind()
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    (OBS_DIR / "logs").mkdir(parents=True, exist_ok=True)
    _migrate_legacy()


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []
    take = min(size, max(max_lines, 1) * 900)
    try:
        with path.open("rb") as fh:
            if size > take:
                fh.seek(-take, 2)
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > take and lines:
        lines = lines[1:]
    return lines[-max_lines:]


def read_jsonl_records(*relative: str, limit: int = 200) -> list[dict]:
    """Tail-merge Jarvis + leftover Ada jsonl, oldest first, then clip to limit."""
    import json

    rows: list[dict] = []
    want = max(limit * 4, 200)
    for path in jsonl_read_paths(*relative):
        if not path.exists():
            continue
        for line in _tail_lines(path, want):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
    rows.sort(key=lambda r: float(r.get("ts") or 0))
    return rows[-max(1, limit) :]


def jsonl_read_paths(*relative: str) -> list[Path]:
    """Jarvis file first, then leftover Ada file if it still has bytes."""
    rel = Path(*relative)
    paths = [obs_dir() / rel]
    legacy = legacy_obs_dir() / rel
    try:
        if legacy.exists() and legacy.resolve() != paths[0].resolve():
            paths.append(legacy)
    except OSError:
        pass
    return paths
