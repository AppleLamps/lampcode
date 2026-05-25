from __future__ import annotations

import anyio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from agent.mcp_server.runner import format_agent_run_result, run_agent_tool
from agent.mcp_server.schema import AGENT_RUN_INPUT_SCHEMA, AGENT_RUN_TOOL_NAME

SERVER_NAME = "agent-cli"
SERVER_VERSION = "2.8.0"


def create_server() -> Server:
    server = Server(SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=AGENT_RUN_TOOL_NAME,
                description="Run one agent-cli turn with the given prompt (headless).",
                inputSchema=AGENT_RUN_INPUT_SCHEMA,
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        if name != AGENT_RUN_TOOL_NAME:
            raise ValueError(f"Unknown tool: {name}")
        result = run_agent_tool(arguments or {})
        return [types.TextContent(type="text", text=format_agent_run_result(result))]

    return server


async def _run_async() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def run_stdio_server() -> None:
    anyio.run(_run_async)
