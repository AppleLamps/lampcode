from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.lsp.position import line_col_to_lsp
from agent.lsp.rpc import JsonRpcProcess, LspRpcError
from agent.lsp.servers import LanguageServerSpec, server_spec_for_path
from agent.lsp.uri import path_to_uri


@dataclass
class DocumentState:
    version: int = 1
    text: str = ""


class LanguageServerSession:
    """One language server process for a workspace."""

    def __init__(
        self,
        spec: LanguageServerSpec,
        workspace: Path,
        *,
        timeout_sec: float = 30.0,
    ) -> None:
        self.spec = spec
        self.workspace = workspace.resolve()
        self._rpc = JsonRpcProcess(
            spec.command,
            cwd=str(self.workspace),
            timeout_sec=timeout_sec,
        )
        self._documents: dict[str, DocumentState] = {}
        self._initialized = False

    def start(self) -> None:
        self._rpc.start()
        root_uri = path_to_uri(self.workspace)
        self._rpc.request(
            "initialize",
            {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "documentSymbol": {"dynamicRegistration": False},
                        "hover": {"dynamicRegistration": False},
                        "rename": {"dynamicRegistration": False},
                        "publishDiagnostics": {},
                    },
                    "workspace": {"symbol": {"dynamicRegistration": False}},
                },
            },
        )
        self._rpc.notify("initialized", {})
        self._initialized = True

    def close(self) -> None:
        if self._initialized:
            try:
                self._rpc.notify("exit")
            except Exception:
                pass
        self._rpc.close()

    def sync_document(self, path: Path, text: str) -> str:
        uri = path_to_uri(path)
        state = self._documents.get(uri)
        if state is None:
            self._documents[uri] = DocumentState(version=1, text=text)
            self._rpc.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": self.spec.language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
        else:
            state.version += 1
            state.text = text
            self._rpc.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": state.version},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri

    def _position_params(self, uri: str, text: str, line: int, character: int) -> dict[str, Any]:
        line_0, char_0 = line_col_to_lsp(line, character, text)
        return {
            "textDocument": {"uri": uri},
            "position": {"line": line_0, "character": char_0},
        }

    def definition(
        self, path: Path, text: str, *, line: int, character: int
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        uri = self.sync_document(path, text)
        result = self._rpc.request(
            "textDocument/definition",
            self._position_params(uri, text, line, character),
        )
        return result

    def references(
        self, path: Path, text: str, *, line: int, character: int
    ) -> list[dict[str, Any]] | None:
        uri = self.sync_document(path, text)
        result = self._rpc.request(
            "textDocument/references",
            {**self._position_params(uri, text, line, character), "context": {"includeDeclaration": True}},
        )
        return result if isinstance(result, list) else None

    def document_symbols(self, path: Path, text: str) -> list[dict[str, Any]] | None:
        uri = self.sync_document(path, text)
        result = self._rpc.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        return result if isinstance(result, list) else None

    def hover(self, path: Path, text: str, *, line: int, character: int) -> Any:
        uri = self.sync_document(path, text)
        return self._rpc.request(
            "textDocument/hover",
            self._position_params(uri, text, line, character),
        )

    def workspace_symbol(self, query: str) -> list[dict[str, Any]] | None:
        result = self._rpc.request("workspace/symbol", {"query": query})
        return result if isinstance(result, list) else None

    def rename(
        self, path: Path, text: str, *, line: int, character: int, new_name: str
    ) -> dict[str, Any] | None:
        uri = self.sync_document(path, text)
        result = self._rpc.request(
            "textDocument/rename",
            {**self._position_params(uri, text, line, character), "newName": new_name},
        )
        return result if isinstance(result, dict) else None

    def completion(
        self, path: Path, text: str, *, line: int, character: int
    ) -> list[dict[str, Any]] | None:
        uri = self.sync_document(path, text)
        result = self._rpc.request(
            "textDocument/completion",
            self._position_params(uri, text, line, character),
        )
        if isinstance(result, dict) and "items" in result:
            return result["items"]
        return result if isinstance(result, list) else None

    def pull_diagnostics(self, path: Path, text: str) -> list[dict[str, Any]]:
        uri = self.sync_document(path, text)
        for method in (
            "textDocument/diagnostic",
            "textDocument/pullDiagnostic",
        ):
            try:
                result = self._rpc.request(
                    method,
                    {"textDocument": {"uri": uri}},
                )
                if isinstance(result, dict) and "items" in result:
                    return result["items"]
                if isinstance(result, dict) and "kind" in result:
                    related = result.get("relatedDocuments") or {}
                    items: list[dict[str, Any]] = []
                    for doc_items in related.values():
                        if isinstance(doc_items, dict):
                            items.extend(doc_items.get("items", []))
                    if items:
                        return items
                if isinstance(result, list):
                    return result
            except LspRpcError:
                continue
        return list(self._cached_diagnostics(uri))

    def _cached_diagnostics(self, uri: str) -> list[dict[str, Any]]:
        return self._rpc.diagnostics_for_uri(uri)


@dataclass
class WorkspaceLspRouter:
    """Lazy language-server sessions keyed by server name."""

    workspace: Path
    timeout_sec: float = 30.0
    _sessions: dict[str, LanguageServerSession] = field(default_factory=dict)

    def close_all(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    def _session_for_path(self, path: Path) -> LanguageServerSession:
        spec = server_spec_for_path(path)
        if not spec:
            raise LspRpcError(f"No language server for {path.suffix}")
        if spec.name not in self._sessions:
            session = LanguageServerSession(spec, self.workspace, timeout_sec=self.timeout_sec)
            session.start()
            self._sessions[spec.name] = session
        return self._sessions[spec.name]

    def read_file_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def resolve_path(self, rel_path: str) -> Path:
        from agent.paths import resolve_path_within_cwd

        return resolve_path_within_cwd(self.workspace, rel_path)

    def any_session(self) -> LanguageServerSession:
        """Return a started session (prefer Python, then TypeScript)."""
        from agent.lsp.servers import python_server_spec, typescript_server_spec

        for spec in (python_server_spec(), typescript_server_spec()):
            if not spec:
                continue
            ext = ".py" if spec.language_id == "python" else ".ts"
            probe = self.workspace / f"__lsp_probe{ext}"
            if not probe.is_file():
                probe.write_text("# lsp\n" if ext == ".py" else "// lsp\n", encoding="utf-8")
            return self._session_for_path(probe)
        raise LspRpcError(
            "No language server on PATH. Install pyright or typescript-language-server."
        )
