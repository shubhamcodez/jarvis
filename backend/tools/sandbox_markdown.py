"""
Format sandbox stdout as Markdown with embedded images for the chat UI.

Convention (preferred): print one line per figure:
  ADA_IMAGE_PNG:<base64 with no newlines>
  ADA_IMAGE_JPEG:...
  (Legacy JARVIS_IMAGE_* lines are still accepted.)
Heuristic: a single line that looks like PNG/JPEG base64 (e.g. raw print of b64) is also embedded.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import List

_TAG_RE = re.compile(
    r"^(?:ADA|JARVIS)_IMAGE_(PNG|JPE?G|GIF|WEBP):\s*([A-Za-z0-9+/=\s]+)\s*$",
    re.IGNORECASE,
)
_PREVIEW_RE = re.compile(
    r"^(?:ADA|JARVIS)_PREVIEW_(HTML|SVG|MERMAID):\s*(.*)\s*$",
    re.IGNORECASE,
)

# Markdown images with embedded base64 (assistant reply after stdout_to_markdown_body)
_CHART_MD_RE = re.compile(r"!\[[^\]]*]\(data:image/[^)]+\)", re.IGNORECASE)


def redact_markdown_chart_embeds(text: str) -> str:
    """Replace ![…](data:image/…) with a placeholder (e.g. trace logs)."""
    if not text:
        return text
    return _CHART_MD_RE.sub("![chart](redacted)", text)


def _mime_for_tag(tag: str) -> str:
    t = tag.upper().replace("JPEG", "JPG")
    if t == "JPG":
        return "image/jpeg"
    if t == "GIF":
        return "image/gif"
    if t == "WEBP":
        return "image/webp"
    return "image/png"


def _line_is_png_base64(line: str) -> bool:
    if len(line) < 200 or len(line) > 50_000_000:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", line):
        return False
    if not line.startswith("iVBOR"):
        return False
    try:
        raw = base64.b64decode(line, validate=True)
    except (binascii.Error, ValueError):
        try:
            raw = base64.b64decode(line)
        except (binascii.Error, ValueError):
            return False
    return len(raw) >= 8 and raw[:8] == b"\x89PNG\r\n\x1a\n"


def _line_is_jpeg_base64(line: str) -> bool:
    if len(line) < 200 or len(line) > 50_000_000:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", line):
        return False
    if not line.startswith("/9j"):
        return False
    try:
        raw = base64.b64decode(line, validate=True)
    except (binascii.Error, ValueError):
        try:
            raw = base64.b64decode(line)
        except (binascii.Error, ValueError):
            return False
    return len(raw) >= 3 and raw[:3] == b"\xff\xd8\xff"


def stdout_to_markdown_parts(stdout: str) -> List[str]:
    """
    Split stdout into ordered markdown fragments: alternating ``` text blocks and ![plot](data:...) images.
    """
    text = (stdout or "").replace("\r\n", "\n")
    if not text.strip():
        return ["```\n(no output)\n```"]

    lines = text.split("\n")
    parts: list[str] = []
    buf: list[str] = []

    def flush_buf() -> None:
        if not buf:
            return
        block = "\n".join(buf).rstrip("\n")
        buf.clear()
        if block:
            parts.append(f"```\n{block}\n```")

    for line in lines:
        stripped = line.strip()
        pm = _PREVIEW_RE.match(stripped) if stripped else None
        if pm:
            flush_buf()
            kind = (pm.group(1) or "HTML").lower()
            payload = (pm.group(2) or "").strip()
            if payload:
                try:
                    decoded = base64.b64decode(payload).decode("utf-8", errors="replace")
                    if "<" in decoded:
                        payload = decoded
                except Exception:
                    pass
                fence = "svg" if kind == "svg" else "mermaid" if kind == "mermaid" else "html"
                parts.append(f"```{fence}\n{payload}\n```")
            continue
        m = _TAG_RE.match(stripped) if stripped else None
        if m:
            flush_buf()
            mime = _mime_for_tag(m.group(1))
            b64 = re.sub(r"\s+", "", m.group(2))
            if len(b64) > 80:
                parts.append(f"![Chart](data:{mime};base64,{b64})")
            continue
        if stripped and _line_is_png_base64(stripped):
            flush_buf()
            parts.append(f"![Chart](data:image/png;base64,{stripped})")
            continue
        if stripped and _line_is_jpeg_base64(stripped):
            flush_buf()
            parts.append(f"![Chart](data:image/jpeg;base64,{stripped})")
            continue
        buf.append(line)

    flush_buf()
    return parts if parts else ["```\n(no output)\n```"]


def stdout_to_markdown_body(stdout: str) -> str:
    """Single markdown string for the assistant reply (images render in ReactMarkdown)."""
    return "\n\n".join(stdout_to_markdown_parts(stdout))


def redact_image_stdout(text: str) -> str:
    """
    Strip chart payloads from stdout for tool cards, traces, step WebSocket payloads, and LLM retry context.
    """
    if not text or not str(text).strip():
        return text if isinstance(text, str) else ""
    lines = str(text).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and _TAG_RE.match(stripped):
            out.append("[chart image hidden]")
            continue
        if stripped and (_line_is_png_base64(stripped) or _line_is_jpeg_base64(stripped)):
            out.append("[chart image hidden]")
            continue
        out.append(line)
    return "\n".join(out)


def redact_sandbox_result_dict(result: dict) -> dict:
    """Copy of sandbox result dict with stdout redacted (safe to json.dumps for logs)."""
    out = dict(result)
    s = out.get("stdout")
    if isinstance(s, str):
        out["stdout"] = redact_image_stdout(s)
    return out
