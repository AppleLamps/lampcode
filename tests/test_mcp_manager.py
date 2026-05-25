from __future__ import annotations

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
