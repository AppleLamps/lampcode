"""Stdio MCP server exposing agent-cli as an external tool."""

from agent.mcp_server.runner import run_agent_tool
from agent.mcp_server.server import create_server, run_stdio_server

__all__ = ["create_server", "run_agent_tool", "run_stdio_server"]
