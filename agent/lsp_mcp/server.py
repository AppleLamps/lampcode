from __future__ import annotations

import anyio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from agent.lsp_mcp.handlers import handle_tool, shutdown_router
from agent.lsp_mcp.schema import TOOL_SCHEMAS

SERVER_NAME = "agent-cli-lsp"
SERVER_VERSION = "2.11.0"


def create_server() -> Server:
    server = Server(SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=meta["description"],
                inputSchema=meta["inputSchema"],
            )
            for name, meta in TOOL_SCHEMAS.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        text = handle_tool(name, arguments)
        return [types.TextContent(type="text", text=text)]

    return server


async def _run_async() -> None:
    server = create_server()
    try:
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
    finally:
        shutdown_router()


def run_stdio_server() -> None:
    anyio.run(_run_async)
