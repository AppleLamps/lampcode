from __future__ import annotations

from typing import Any

PATH_PARAM = {
    "type": "string",
    "description": "File path relative to workspace root.",
}

LINE_PARAM = {
    "type": "integer",
    "description": "1-based line number.",
}

CHAR_PARAM = {
    "type": "integer",
    "description": "0-based character offset on the line (UTF-8 byte index for ASCII identifiers).",
}

NAV_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": PATH_PARAM,
        "line": LINE_PARAM,
        "character": CHAR_PARAM,
    },
    "required": ["path"],
}

PATH_ONLY: dict[str, Any] = {
    "type": "object",
    "properties": {"path": PATH_PARAM},
    "required": ["path"],
}

LSP_DEFINITION = "lsp_definition"
LSP_REFERENCES = "lsp_references"
LSP_DOCUMENT_SYMBOLS = "lsp_document_symbols"
LSP_HOVER = "lsp_hover"
LSP_WORKSPACE_SYMBOL = "lsp_workspace_symbol"
LSP_DIAGNOSTICS = "lsp_diagnostics"
LSP_RENAME = "lsp_rename"

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    LSP_DEFINITION: {
        "description": "Go to definition via language server (Python/TS/JS).",
        "inputSchema": NAV_PARAMS,
    },
    LSP_REFERENCES: {
        "description": "Find references via language server.",
        "inputSchema": NAV_PARAMS,
    },
    LSP_DOCUMENT_SYMBOLS: {
        "description": "List symbols in a file via language server.",
        "inputSchema": PATH_ONLY,
    },
    LSP_HOVER: {
        "description": "Type/signature hover via language server.",
        "inputSchema": NAV_PARAMS,
    },
    LSP_WORKSPACE_SYMBOL: {
        "description": "Search symbols across the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    LSP_DIAGNOSTICS: {
        "description": "Pull diagnostics for a file (errors/warnings).",
        "inputSchema": PATH_ONLY,
    },
    LSP_RENAME: {
        "description": "Rename symbol; returns multi-file edit plan for apply_patch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": PATH_PARAM,
                "line": LINE_PARAM,
                "character": CHAR_PARAM,
                "new_name": {"type": "string"},
            },
            "required": ["path", "new_name"],
        },
    },
}
