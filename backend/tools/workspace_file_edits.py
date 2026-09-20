"""
Parse proposed project file updates from model output.

Models are instructed to wrap full-file replacements in fences:

```jarvis-file:relative/path/to/file.ext
<complete new file contents>
```

`ada-file:` fences from older chats are still accepted.
Parsed edits are returned separately from the user-visible reply (fences stripped).
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Opening fence on its own line. Closer is a line that is exactly ```, or
# ``` at the end of the last content line (models often omit the newline).
_OPEN = re.compile(r"^```\s*(?:jarvis|ada)-file:([^\n`]+)\s*$")
_CLOSE = re.compile(r"^```\s*$")


def normalize_workspace_relative_path(raw: str) -> Optional[str]:
    p = (raw or "").strip().replace("\\", "/").lstrip("/")
    if not p or p.startswith("..") or "/../" in f"/{p}/":
        return None
    if ".." in p.split("/"):
        return None
    if re.match(r"^[A-Za-z]:", p) or p.startswith("//"):
        return None
    return p


def extract_workspace_file_edits(text: str) -> tuple[str, list[dict[str, Any]]]:
    """
    Strip jarvis-file / ada-file fences from text and return ({clean_markdown}, [{path, content}, ...]).
    Order preserved. Duplicate paths: last wins (caller may dedupe).
    """
    if not text:
        return "", []

    by_path: dict[str, str] = {}
    out: list[str] = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        raw = lines[i]
        opened = _OPEN.match(raw.rstrip("\r\n"))
        if not opened:
            out.append(raw)
            i += 1
            continue
        rel = normalize_workspace_relative_path(opened.group(1))
        body_parts: list[str] = []
        i += 1
        closed = False
        while i < len(lines):
            bline = lines[i]
            bstrip = bline.rstrip("\r\n")
            if _CLOSE.match(bstrip):
                closed = True
                i += 1
                break
            if bstrip.endswith("```"):
                body_parts.append(bstrip[:-3])
                closed = True
                i += 1
                break
            body_parts.append(bline)
            i += 1
        if not closed:
            out.append(raw)
            out.extend(body_parts)
            continue
        if rel is not None:
            by_path[rel] = "".join(body_parts)
        # #region agent log
        try:
            import json
            import time
            from pathlib import Path as _P

            _p = _P(__file__).resolve().parents[2] / "debug-ff2cb7.log"
            with _p.open("a", encoding="utf-8") as _f:
                _f.write(
                    json.dumps(
                        {
                            "sessionId": "ff2cb7",
                            "timestamp": int(time.time() * 1000),
                            "location": "workspace_file_edits.py:extract",
                            "message": "extracted fence",
                            "hypothesisId": "A",
                            "data": {
                                "path": rel,
                                "closed": closed,
                                "body_len": len("".join(body_parts)),
                                "edits_so_far": len(by_path),
                            },
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
    clean = "".join(out)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    edits = [{"path": p, "content": c} for p, c in by_path.items()]
    return clean, edits
