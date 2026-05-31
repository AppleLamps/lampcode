from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.mcp.manager import McpManager
from agent.settings import McpConfig, McpServerConfig, McpSettings


async def _mock_session_factory(cfg: McpServerConfig) -> tuple[Any, list[dict[str, Any]]]:
    tools = [
        {
            "name": "list_files",
            "description": "List files",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
    return object(), tools


def test_connect_and_register_tools() -> None:
    config = McpConfig(
        servers={
            "filesystem": McpServerConfig(
                name="filesystem",
                command="mock",
                args=[],
                enabled=True,
                require_approval=True,
            )
        },
        settings=McpSettings(tool_name_prefix=True),
    )
    manager = McpManager(config, session_factory=_mock_session_factory)
    connected: list[str] = []
    manager.connect_all(on_connected=connected.append)
    assert connected == ["filesystem"]
    assert "mcp__filesystem__list_files" in manager.tool_map
    manager.disconnect_all()


def test_call_tool_mock() -> None:
    class FakeResult:
        isError = False
        content = [type("B", (), {"text": "ok"})()]

    class FakeSession:
        async def call_tool(self, name, arguments):
            return FakeResult()

    config = McpConfig(
        servers={
            "filesystem": McpServerConfig(
                name="filesystem", command="mock", enabled=True
            )
        }
    )

    async def factory(cfg):
        return FakeSession(), [
            {"name": "list_files", "description": "", "inputSchema": {}}
        ]

    manager = McpManager(config, session_factory=factory)
    manager.connect_all()
    output, code, err = manager.call_tool("mcp__filesystem__list_files", {})
    assert code == 0
    assert output == "ok"
    manager.disconnect_all()


def test_server_failure_isolation() -> None:
    config = McpConfig(
        servers={
            "bad": McpServerConfig(name="bad", command="x", enabled=True),
            "good": McpServerConfig(name="good", command="y", enabled=True),
        }
    )

    async def factory(cfg):
        if cfg.name == "bad":
            raise RuntimeError("boom")
        return object(), [{"name": "t", "description": "", "inputSchema": {}}]

    manager = McpManager(config, session_factory=factory)
    failed: list[str] = []
    manager.connect_all(on_failed=lambda s, e: failed.append(s))
    assert "bad" in failed
    assert "mcp__good__t" in manager.tool_map
    manager.disconnect_all()


def test_lsp_mcp_launch_options_pin_project_workspace(tmp_path: Path) -> None:
    cfg = McpServerConfig(
        name="lsp",
        command="agent",
        args=["lsp-mcp"],
        enabled=True,
        require_approval=False,
    )
    manager = McpManager(McpConfig(servers={"lsp": cfg}, project_cwd=tmp_path))

    args, env, cwd = manager._stdio_launch_options(cfg)

    assert args == ["lsp-mcp", "--workspace", str(tmp_path)]
    assert env["WORKSPACE_ROOT"] == str(tmp_path)
    assert cwd == tmp_path


def test_lsp_mcp_launch_options_respect_explicit_workspace(tmp_path: Path) -> None:
    cfg = McpServerConfig(
        name="lsp",
        command="agent",
        args=["lsp-mcp", "--workspace", "/explicit"],
        enabled=True,
        require_approval=False,
    )
    manager = McpManager(McpConfig(servers={"lsp": cfg}, project_cwd=tmp_path))

    args, env, cwd = manager._stdio_launch_options(cfg)

    assert args == ["lsp-mcp", "--workspace", "/explicit"]
    assert env["WORKSPACE_ROOT"] == "/explicit"
    assert cwd == tmp_path


def test_lsp_mcp_launch_options_normalize_relative_workspace(tmp_path: Path) -> None:
    cfg = McpServerConfig(
        name="lsp",
        command="agent",
        args=["lsp-mcp", "--workspace", "."],
        enabled=True,
        require_approval=False,
    )
    manager = McpManager(McpConfig(servers={"lsp": cfg}, project_cwd=tmp_path))

    args, env, cwd = manager._stdio_launch_options(cfg)

    assert args == ["lsp-mcp", "--workspace", str(tmp_path)]
    assert env["WORKSPACE_ROOT"] == str(tmp_path)
    assert cwd == tmp_path


def test_non_lsp_mcp_launch_options_do_not_set_project_cwd(tmp_path: Path) -> None:
    cfg = McpServerConfig(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "."],
        enabled=True,
    )
    manager = McpManager(McpConfig(servers={"filesystem": cfg}, project_cwd=tmp_path))

    args, env, cwd = manager._stdio_launch_options(cfg)

    assert args == ["-y", "@modelcontextprotocol/server-filesystem", "."]
    assert "WORKSPACE_ROOT" not in env
    assert cwd is None
