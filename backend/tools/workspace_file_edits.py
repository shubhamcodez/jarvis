"""
Parse proposed project file updates from model output.

Models are instructed to wrap full-file replacements in fences:

```ada-file:relative/path/to/file.ext
<complete new file contents>
```

Parsed edits are returned separately from the user-visible reply (fences stripped).
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Opening fence at BOL; close only on a line that is exactly ```
_ADA_FILE_BLOCK = re.compile(
    r"^```\s*ada-file:([^\n`]+)\s*\n(.*?\n)```\s*$",
    re.MULTILINE | re.DOTALL,
)


def normalize_workspace_relative_path(raw: str) -> Optional[str]:
    p = (raw or "").strip().replace("\\", "/").lstrip("/")
    if not p or p.startswith("..") or "/../" in f"/{p}/":
        return None
    if ".." in p.split("/"):
        return None
    if re.match(r"^[A-Za-z]:", p) or p.startswith("//"):
        return None
    return p


def extract_ada_file_edits(text: str) -> tuple[str, list[dict[str, Any]]]:
    """
    Strip ada-file fences from text and return ({clean_markdown}, [{path, content}, ...]).
    Order preserved. Duplicate paths: last wins (caller may dedupe).
    """
    if not text:
        return "", []

    by_path: dict[str, str] = {}

    def _collect(m: re.Match) -> str:
        rel = normalize_workspace_relative_path(m.group(1))
        body = m.group(2)
        if rel is not None and body is not None:
            by_path[rel] = body
        return ""

    clean = _ADA_FILE_BLOCK.sub(_collect, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    edits = [{"path": p, "content": c} for p, c in by_path.items()]
    return clean, edits
