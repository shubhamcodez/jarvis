"""Structured JSON application logs, correlated with the current span/trace."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .redact import redact_text, redact_value
from .spans import current_trace_id


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage(), max_len=1500),
        }
        tid = current_trace_id()
        if tid:
            payload["trace_id"] = tid
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info), max_len=2000)
        extra = {k: v for k, v in record.__dict__.items() if k not in _STD and not k.startswith("_")}
        if extra:
            payload["extra"] = redact_value(extra)
        return json.dumps(payload, ensure_ascii=False)


_STD = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
}


_CONFIGURED = False


def configure_struct_logging() -> None:
    """Attach a JSON formatter to ada.* loggers once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("jarvis")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers if h.formatter):
        _CONFIGURED = True
        return
    formatter = JsonFormatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)
    try:
        from .config import OBS_DIR, ensure_dirs

        ensure_dirs()
        log_dir = OBS_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        fh = RotatingFileHandler(
            log_dir / "app.jsonl",
            maxBytes=8_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception:
        pass
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _CONFIGURED = True


def list_recent_logs(limit: int = 200) -> list[dict]:
    """Tail structured application logs from disk."""
    try:
        from .config import OBS_DIR

        path = OBS_DIR / "logs" / "app.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, limit) :]:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"raw": line[:500]})
        return out
    except OSError:
        return []
