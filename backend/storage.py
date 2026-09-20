"""Chats storage path: get/set via config file."""
from pathlib import Path

from config import chats_config_path, chats_dir


_FORBIDDEN_ROOTS = frozenset({
    Path("/"),
    Path("C:/"),
    Path("C:\\"),
})


def get_chats_storage_path() -> str:
    return str(chats_dir())


def set_chats_storage_path(path: str) -> str:
    path = (path or "").strip()
    if not path:
        raise ValueError("Path cannot be empty.")
    p = Path(path).expanduser()
    try:
        resolved = p.resolve()
    except OSError as exc:
        raise ValueError(f"Path cannot be resolved: {exc}") from exc
    # Reject drive roots and obviously dangerous locations.
    if resolved in _FORBIDDEN_ROOTS or len(resolved.parts) < 2:
        raise ValueError("Refusing to use a filesystem root as the chats directory.")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create chats directory: {exc}") from exc
    if not resolved.is_dir():
        raise ValueError("Chats storage path must be a directory.")
    probe = resolved / ".ada-write-check"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"Chats directory is not writable: {exc}") from exc
    marker = chats_config_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(str(resolved), encoding="utf-8")
    tmp.replace(marker)
    from memory.chat_log import clear_current_chat
    clear_current_chat()
    return str(resolved)
