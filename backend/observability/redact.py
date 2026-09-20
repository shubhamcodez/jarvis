"""Redact secrets and obvious PII before traces, spans, or structured logs hit disk."""
from __future__ import annotations

import re

_KEY_LINE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token|authorization|bearer)\b([\"']?\s*[:=]\s*)([^\s,;]{6,})",
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+=/]{8,}")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_SK = re.compile(r"\b(?:sk|xai)-[A-Za-z0-9]{12,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
_GITHUB = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")
_AWS = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|access_token|api_key|key|secret|password)=)[^&\s]+")


def redact_text(text: str, *, emails: bool = False, max_len: int = 4000) -> str:
    raw = text or ""
    raw = _KEY_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", raw)
    raw = _BEARER.sub("Bearer ***", raw)
    raw = _SK.sub("***", raw)
    raw = _JWT.sub("[jwt]", raw)
    raw = _GITHUB.sub("[gh_token]", raw)
    raw = _AWS.sub("[aws_key]", raw)
    raw = _QUERY_SECRET.sub(r"\1***", raw)
    if emails:
        raw = _EMAIL.sub("[email]", raw)
    if len(raw) > max_len:
        raw = raw[: max_len - 1] + "…"
    return raw


def redact_value(value):
    if isinstance(value, str):
        return redact_text(value, max_len=2000)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value[:40]]
    return value
