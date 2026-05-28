from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.lsp.client import WorkspaceLspRouter
from agent.lsp.format import (
    format_diagnostics,
    format_document_symbols,
    format_hover,
    format_locations,
    format_workspace_edit,
    format_workspace_symbols,
)
from agent.lsp.rpc import LspRpcError
from agent.lsp_mcp.schema import (
    LSP_DEFINITION,
    LSP_DIAGNOSTICS,
    LSP_DOCUMENT_SYMBOLS,
    LSP_HOVER,
    LSP_REFERENCES,
    LSP_RENAME,
    LSP_WORKSPACE_SYMBOL,
)

_router: WorkspaceLspRouter | None = None


def get_router() -> WorkspaceLspRouter:
    global _router
    if _router is None:
        root = Path(os.environ.get("WORKSPACE_ROOT", os.getcwd())).resolve()
        _router = WorkspaceLspRouter(workspace=root)
    return _router


def shutdown_router() -> None:
    global _router
    if _router is not None:
        _router.close_all()
        _router = None


def handle_tool(name: str, arguments: dict[str, Any] | None) -> str:
    args = arguments or {}
    router = get_router()
    try:
        if name == LSP_DEFINITION:
            return _definition(router, args)
        if name == LSP_REFERENCES:
            return _references(router, args)
        if name == LSP_DOCUMENT_SYMBOLS:
            return _document_symbols(router, args)
        if name == LSP_HOVER:
            return _hover(router, args)
        if name == LSP_WORKSPACE_SYMBOL:
            return _workspace_symbol(router, args)
        if name == LSP_DIAGNOSTICS:
            return _diagnostics(router, args)
        if name == LSP_RENAME:
            return _rename(router, args)
        return f"Unknown LSP tool: {name}"
    except LspRpcError as exc:
        return f"LSP error: {exc}"
    except ValueError as exc:
        return f"Invalid request: {exc}"
    except OSError as exc:
        return f"File error: {exc}"


def _load(router: WorkspaceLspRouter, args: dict[str, Any]) -> tuple[Path, str, int, int]:
    rel = args.get("path", "")
    if not rel:
        raise ValueError("path is required")
    path = router.resolve_path(rel)
    text = router.read_file_text(path)
    line = int(args.get("line", 1))
    character = int(args.get("character", 0))
    return path, text, line, character


def _definition(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    path, text, line, character = _load(router, args)
    session = router._session_for_path(path)
    result = session.definition(path, text, line=line, character=character)
    if not result:
        return "No definition found."
    if isinstance(result, list):
        body = format_locations(result, workspace=router.workspace)
    else:
        body = format_locations([result], workspace=router.workspace)
    return f"Definitions:\n{body}"


def _references(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    path, text, line, character = _load(router, args)
    session = router._session_for_path(path)
    result = session.references(path, text, line=line, character=character)
    if not result:
        return "No references found."
    return f"References:\n{format_locations(result, workspace=router.workspace)}"


def _document_symbols(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    path, text, _, _ = _load(router, args)
    session = router._session_for_path(path)
    symbols = session.document_symbols(path, text)
    rel = path.relative_to(router.workspace).as_posix()
    return format_document_symbols(symbols, path=rel)


def _hover(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    path, text, line, character = _load(router, args)
    session = router._session_for_path(path)
    result = session.hover(path, text, line=line, character=character)
    return format_hover(result)


def _workspace_symbol(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "query is required"
    session = router.any_session()
    symbols = session.workspace_symbol(query)
    return format_workspace_symbols(symbols, workspace=router.workspace)


def _diagnostics(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    path, text, _, _ = _load(router, args)
    session = router._session_for_path(path)
    items = session.pull_diagnostics(path, text)
    rel = path.relative_to(router.workspace).as_posix()
    return format_diagnostics(items, path=rel)


def _rename(router: WorkspaceLspRouter, args: dict[str, Any]) -> str:
    new_name = str(args.get("new_name", "")).strip()
    if not new_name:
        return "new_name is required"
    path, text, line, character = _load(router, args)
    session = router._session_for_path(path)
    edit = session.rename(path, text, line=line, character=character, new_name=new_name)
    return format_workspace_edit(edit, workspace=router.workspace)
