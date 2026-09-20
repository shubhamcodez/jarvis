"""One-shot source rename: Ada → Jarvis. Run from repo root."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIR = {
    ".git",
    "node_modules",
    "__pycache__",
    "dist",
    "target",
    "ada-artifacts",
    "jarvis-artifacts",
    "models",
    ".venv",
    "venv",
}
SKIP_SUFFIX = {".pyc", ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".exe", ".dll"}
SKIP_NAME = {
    "last_run_humaneval.json",
    "last_run_swe.json",
    "package-lock.json",
    "Cargo.lock",
    "_rename_ada_to_jarvis.py",
}

# Word Ada → Jarvis (not ADA.md / ada-file)
ADA_WORD = re.compile(r"\bAda\b")
# Possessive already covered (Ada's)

# Common identifier / path tokens
PAIRS = [
    ("notifyAda", "notifyJarvis"),
    ("selectAda", "selectJarvis"),
    ("stripAdaFileFencesForDisplay", "stripJarvisFileFencesForDisplay"),
    ("extract_ada_file_edits", "extract_workspace_file_edits"),
    ("X-Ada-Token", "X-Jarvis-Token"),
    ("x-ada-token", "x-jarvis-token"),
    ("Ada.jpg", "Jarvis.jpg"),
    ("ada-chat.md", "jarvis-chat.md"),
    ("ada-backend", "jarvis-backend"),
    ("ada-pylsp", "jarvis-pylsp"),
    ("ada-config.yaml", "jarvis-config.yaml"),
    ("ada-grep-root.txt", "jarvis-grep-root.txt"),
    ("ada-observability", "jarvis-observability"),
    ("ada-artifacts", "jarvis-artifacts"),
    ("ada-uploads", "jarvis-uploads"),
    ("ada-api-token", "jarvis-api-token"),
    ("ada-local", "jarvis-local"),
    ("ada-oauth-v1", "jarvis-oauth-v1"),
    ("ada-desktop", "jarvis-desktop"),
    ("ada.memory", "jarvis.memory"),
    ("You are Ada", "You are Jarvis"),
]


def should_skip(path: Path) -> bool:
    if path.name in SKIP_NAME:
        return True
    if path.suffix.lower() in SKIP_SUFFIX:
        return True
    parts = set(path.parts)
    if parts & SKIP_DIR:
        return True
    if "benchmarks" in path.parts and "data" in path.parts and path.suffix == ".json":
        return True
    return False


def transform(text: str) -> str:
    for a, b in PAIRS:
        text = text.replace(a, b)
    text = ADA_WORD.sub("Jarvis", text)
    return text


def main() -> None:
    n = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = transform(raw)
        if new != raw:
            path.write_text(new, encoding="utf-8")
            n += 1
            print(path.relative_to(ROOT))
    print(f"updated {n} files")


if __name__ == "__main__":
    main()
