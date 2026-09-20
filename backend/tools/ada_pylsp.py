"""
Minimal stdio LSP for Jarvis: initialize → didOpen → textDocument/publishDiagnostics.

Uses the ast module (no extra deps). Reports syntax errors and undefined names.
"""
from __future__ import annotations

import ast
import builtins
import json
import sys

_BUILTINS = set(dir(builtins)) | {
    "__name__",
    "__file__",
    "__package__",
    "__doc__",
    "__annotations__",
    "self",
    "cls",
}


def _read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if b":" in line:
            k, v = line.decode("utf-8", errors="replace").split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length") or 0)
    if n <= 0:
        return None
    raw = sys.stdin.buffer.read(n)
    return json.loads(raw.decode("utf-8"))


def _write(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def _notify(method: str, params: dict) -> None:
    _write({"jsonrpc": "2.0", "method": method, "params": params})


def _reply(msg_id, result) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def analyze_python(source: str) -> list[dict]:
    diags: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        line = max(0, int(e.lineno or 1) - 1)
        col = max(0, int(e.offset or 1) - 1)
        diags.append(
            {
                "range": {
                    "start": {"line": line, "character": col},
                    "end": {"line": line, "character": col + 1},
                },
                "severity": 1,
                "source": "jarvis-pylsp",
                "message": e.msg or "SyntaxError",
            }
        )
        return diags

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scopes: list[set[str]] = [_BUILTINS.copy()]

        def _define(self, name: str) -> None:
            if name:
                self.scopes[-1].add(name)

        def _known(self, name: str) -> bool:
            return any(name in s for s in reversed(self.scopes))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._define(node.name)
            inner = set(self.scopes[-1])
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                inner.add(a.arg)
            if node.args.vararg:
                inner.add(node.args.vararg.arg)
            if node.args.kwarg:
                inner.add(node.args.kwarg.arg)
            self.scopes.append(inner)
            self.generic_visit(node)
            self.scopes.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._define(node.name)
            self.scopes.append(set(self.scopes[-1]))
            self.generic_visit(node)
            self.scopes.pop()

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._define(alias.asname or alias.name.split(".", 1)[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                self._define(alias.asname or alias.name)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                self._define(node.id)
            elif isinstance(node.ctx, ast.Load) and not self._known(node.id):
                diags.append(
                    {
                        "range": {
                            "start": {"line": max(0, node.lineno - 1), "character": max(0, node.col_offset)},
                            "end": {
                                "line": max(0, node.lineno - 1),
                                "character": max(0, node.col_offset + len(node.id)),
                            },
                        },
                        "severity": 1,
                        "source": "jarvis-pylsp",
                        "message": f"Undefined name '{node.id}'",
                    }
                )

        def visit_arg(self, node: ast.arg) -> None:
            self._define(node.arg)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                self._define(node.name)
            self.generic_visit(node)

    Visitor().visit(tree)
    return diags


def _language_id(uri: str) -> str:
    if uri.endswith(".py"):
        return "python"
    return "plaintext"


def serve() -> None:
    while True:
        msg = _read_message()
        if msg is None:
            break
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            _reply(
                msg_id,
                {
                    "capabilities": {
                        "textDocumentSync": 1,
                    },
                    "serverInfo": {"name": "jarvis-pylsp", "version": "1"},
                },
            )
        elif method == "initialized":
            continue
        elif method == "textDocument/didOpen":
            doc = (params.get("textDocument") or {})
            uri = doc.get("uri") or "file:///unknown.py"
            text = doc.get("text") or ""
            diags = analyze_python(text) if _language_id(uri) == "python" or uri.endswith(".py") else []
            _notify(
                "textDocument/publishDiagnostics",
                {"uri": uri, "diagnostics": diags[:40]},
            )
        elif method == "shutdown":
            _reply(msg_id, None)
        elif method == "exit":
            break


if __name__ == "__main__":
    serve()
