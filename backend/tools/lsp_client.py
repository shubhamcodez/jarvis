"""Stdio JSON-RPC LSP client. Prefers jedi/pylsp/pyright; falls back to bundled ada-pylsp."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url

from tools.overlay_workspace import OverlayWorkspace

_SERVER_CANDIDATES = (
    ["jedi-language-server"],
    ["pylsp"],
    ["pyright-langserver", "--stdio"],
)


def path_to_uri(path: str | Path) -> str:
    p = Path(path).resolve()
    return "file:" + pathname2url(str(p))


def uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    raw = unquote(parsed.path or "")
    if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    return raw.replace("/", os.sep)


def _pick_server() -> list[str]:
    for cand in _SERVER_CANDIDATES:
        if shutil.which(cand[0]):
            return list(cand)
    return [sys.executable, str(Path(__file__).resolve().parent / "ada_pylsp.py")]


class LspClient:
    def __init__(self, cmd: list[str], root: Path, timeout_sec: float = 4.0):
        self.root = Path(root)
        self.timeout_sec = timeout_sec
        self._id = 0
        self._pending: dict[int, dict[str, Any]] = {}
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(self.root),
        )
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def close(self) -> None:
        self._alive = False
        try:
            if self.proc.poll() is None:
                try:
                    self._request("shutdown", {}, wait=0.4)
                except Exception:
                    pass
                self._notify("exit", {})
                self.proc.terminate()
        except Exception:
            pass
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=1.0)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except Exception:
            pass

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _write(self, payload: dict[str, Any]) -> None:
        if not self.proc.stdin:
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        self.proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any], wait: float = 2.0) -> Any:
        rid = self._next_id()
        self._pending[rid] = {}
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + wait
        while time.time() < deadline:
            rec = self._pending.get(rid)
            if rec and "result" in rec:
                return rec.get("result")
            time.sleep(0.02)
        return None

    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        if not stdout:
            return
        while self._alive and self.proc.poll() is None:
            try:
                headers: dict[str, str] = {}
                while True:
                    line = stdout.readline()
                    if not line:
                        return
                    if line in (b"\r\n", b"\n"):
                        break
                    if b":" in line:
                        k, v = line.decode("utf-8", errors="replace").split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                n = int(headers.get("content-length") or 0)
                if n <= 0:
                    continue
                raw = stdout.read(n)
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                return
            if "id" in msg and "method" not in msg:
                self._pending[int(msg["id"])] = {"result": msg.get("result")}
            elif msg.get("method") == "textDocument/publishDiagnostics":
                params = msg.get("params") or {}
                uri = str(params.get("uri") or "")
                self.diagnostics[uri] = list(params.get("diagnostics") or [])

    def initialize(self) -> None:
        root_uri = path_to_uri(self.root)
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {"textDocument": {"publishDiagnostics": {}}},
                "workspaceFolders": [{"uri": root_uri, "name": self.root.name}],
            },
            wait=2.5,
        )
        self._notify("initialized", {})

    def did_open(self, rel_path: str, text: str, language_id: str = "python") -> None:
        abs_path = (self.root / rel_path.replace("\\", "/")).resolve()
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path_to_uri(abs_path),
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    def wait_diagnostics(self, expected: int, timeout: float | None = None) -> None:
        deadline = time.time() + (timeout if timeout is not None else self.timeout_sec)
        while time.time() < deadline:
            if len(self.diagnostics) >= expected:
                return
            time.sleep(0.03)


def run_lsp_diagnostics(
    workspace: OverlayWorkspace,
    rel_paths: Optional[list[str]] = None,
    *,
    timeout_sec: float = 4.0,
) -> dict[str, Any]:
    paths = rel_paths or [p for p in workspace.changed_paths() if p.endswith(".py")]
    paths = [p for p in paths if p.endswith(".py")]
    if not paths:
        return {"ok": True, "engine": "none", "diagnostics": [], "checked": 0}

    files: dict[str, str] = {}
    for rel in paths[:20]:
        try:
            files[rel] = workspace.raw_text(rel)
        except (OSError, FileNotFoundError, ValueError):
            continue
    if not files:
        return {"ok": True, "engine": "none", "diagnostics": [], "checked": 0}

    cmd = _pick_server()
    engine = Path(cmd[0]).name if cmd else "ada-pylsp"
    if cmd and cmd[-1].endswith("ada_pylsp.py"):
        engine = "ada-pylsp"
    client = LspClient(cmd, workspace.root, timeout_sec=timeout_sec)
    try:
        client.initialize()
        for rel, text in files.items():
            client.did_open(rel, text, "python")
        client.wait_diagnostics(expected=len(files), timeout=timeout_sec)
        out: list[dict[str, Any]] = []
        for uri, items in client.diagnostics.items():
            rel = Path(uri_to_path(uri))
            try:
                rel_s = str(rel.relative_to(workspace.root)).replace("\\", "/")
            except Exception:
                rel_s = rel.name
            for d in items[:20]:
                rng = d.get("range") or {}
                start = rng.get("start") or {}
                out.append(
                    {
                        "path": rel_s,
                        "line": int(start.get("line") or 0) + 1,
                        "col": int(start.get("character") or 0) + 1,
                        "severity": "error" if int(d.get("severity") or 1) <= 1 else "warning",
                        "message": str(d.get("message") or "")[:240],
                        "source": d.get("source") or engine,
                    }
                )
        errors = [d for d in out if d.get("severity") == "error"]
        return {
            "ok": len(errors) == 0,
            "engine": engine,
            "checked": len(files),
            "diagnostics": out[:30],
        }
    except Exception as e:
        return {"ok": True, "engine": engine, "error": str(e), "diagnostics": [], "checked": 0}
    finally:
        client.close()
