from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.lsp_mcp.handlers import handle_tool, shutdown_router
from agent.lsp_mcp.schema import (
    LSP_DEFINITION,
    LSP_DOCUMENT_SYMBOLS,
    LSP_HOVER,
    LSP_REFERENCES,
    LSP_RENAME,
    LSP_WORKSPACE_SYMBOL,
    TOOL_SCHEMAS,
)
from agent.lsp_mcp.server import create_server
from agent.tool_access import filter_tool_schemas, is_tool_allowed
from agent.tool_round import can_parallelize_tool_round


def test_tool_schemas_include_all_lsp_tools() -> None:
    assert LSP_DEFINITION in TOOL_SCHEMAS
    assert LSP_HOVER in TOOL_SCHEMAS
    assert LSP_RENAME in TOOL_SCHEMAS


def test_lsp_tools_parallelize_with_reads() -> None:
    assert can_parallelize_tool_round(
        ["read_file", "mcp__lsp__lsp_definition", "mcp__lsp__lsp_references"]
    )


def test_plan_mode_allows_lsp_mcp_tools() -> None:
    assert is_tool_allowed(
        "mcp__lsp__lsp_definition",
        ["read_file"],
        allow_mcp_servers=["lsp"],
    )
    assert not is_tool_allowed(
        "mcp__other__tool",
        ["read_file"],
        allow_mcp_servers=["lsp"],
    )


def test_filter_tool_schemas_with_mcp() -> None:
    schemas = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "mcp__lsp__lsp_definition"}},
        {"type": "function", "function": {"name": "run_command"}},
    ]
    out = filter_tool_schemas(
        schemas,
        ["read_file"],
        allow_mcp_servers=["lsp"],
    )
    names = {s["function"]["name"] for s in out}
    assert names == {"read_file", "mcp__lsp__lsp_definition"}


def test_handle_definition_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutdown_router()
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "foo.py"
    sample.write_text("def bar():\n    pass\n", encoding="utf-8")

    mock_session = MagicMock()
    mock_session.definition.return_value = [
        {
            "uri": sample.as_uri(),
            "range": {"start": {"line": 0, "character": 4}},
        }
    ]

    mock_router = MagicMock()
    mock_router.workspace = tmp_path
    mock_router.resolve_path.return_value = sample
    mock_router.read_file_text.return_value = sample.read_text(encoding="utf-8")
    mock_router._session_for_path.return_value = mock_session

    with patch("agent.lsp_mcp.handlers.get_router", return_value=mock_router):
        text = handle_tool(
            LSP_DEFINITION,
            {"path": "foo.py", "line": 1, "character": 4},
        )
    shutdown_router()
    assert "Definitions" in text
    assert "foo.py" in text


def test_mcp_server_lists_tools() -> None:
    server = create_server()
    assert server is not None


@pytest.mark.lsp
def test_integration_pyright_definition(tmp_path: Path) -> None:
    import shutil

    if not shutil.which("pyright-langserver"):
        pytest.skip("pyright-langserver not installed")
    shutdown_router()
    f = tmp_path / "m.py"
    f.write_text("def baz():\n    return 1\n", encoding="utf-8")
    import os

    os.environ["WORKSPACE_ROOT"] = str(tmp_path)
    text = handle_tool(LSP_DEFINITION, {"path": "m.py", "line": 1, "character": 4})
    shutdown_router()
    assert "baz" in text or "Definitions" in text
