from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.lsp.format import format_document_symbols, format_locations
from agent.lsp.position import line_col_to_lsp
from agent.lsp.rpc import JsonRpcProcess, LspRpcError
from agent.lsp.servers import language_id_for_path, server_spec_for_path


def test_line_col_to_lsp_ascii() -> None:
    text = "def foo():\n    pass\n"
    line_0, char_0 = line_col_to_lsp(2, 4, text)
    assert line_0 == 1
    assert char_0 == 4


def test_language_id_for_path() -> None:
    assert language_id_for_path(Path("a.py")) == "python"
    assert language_id_for_path(Path("a.ts")) == "typescript"
    assert language_id_for_path(Path("a.js")) == "javascript"


def test_format_locations_relative(tmp_path: Path) -> None:
    mod = tmp_path / "pkg" / "mod.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("x=1\n", encoding="utf-8")
    from agent.lsp.uri import path_to_uri

    items = [
        {
            "uri": path_to_uri(mod),
            "range": {"start": {"line": 0, "character": 0}},
        }
    ]
    out = format_locations(items, workspace=tmp_path)
    assert "mod.py" in out


def test_format_document_symbols() -> None:
    symbols = [
        {
            "name": "foo",
            "kind": 12,
            "range": {"start": {"line": 0, "character": 0}},
        }
    ]
    text = format_document_symbols(symbols, path="mod.py")
    assert "foo" in text


def test_json_rpc_request_response() -> None:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.poll.return_value = None

    response_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    ).encode("utf-8")
    header = f"Content-Length: {len(response_body)}\r\n\r\n".encode("ascii")
    proc.stdout.readline.side_effect = [
        b"Content-Length: 33\r\n",
        b"\r\n",
        response_body,
    ]
    proc.stdout.read.return_value = response_body

    rpc = JsonRpcProcess(["echo"], timeout_sec=2.0)
    rpc._proc = proc
    rpc._reader_thread = None

    with patch.object(rpc, "_wait_response", return_value={"result": {"ok": True}}):
        result = rpc.request("initialize", {})
    assert result == {"ok": True}


def test_server_spec_for_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent.lsp.servers.shutil.which",
        lambda cmd: "/bin/pyright-langserver" if cmd == "pyright-langserver" else None,
    )
    spec = server_spec_for_path(tmp_path / "x.py")
    assert spec is not None
    assert spec.name == "pyright"
