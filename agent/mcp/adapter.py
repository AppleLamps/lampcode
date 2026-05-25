from __future__ import annotations

import re

from agent.settings import McpServerConfig


def sanitize_tool_part(name: str) -> str:
    """Sanitize server/tool names for OpenRouter function names."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def exposed_tool_name(
    server: str, tool: str, *, use_prefix: bool = True
) -> str:
    if use_prefix:
        return f"mcp__{sanitize_tool_part(server)}__{sanitize_tool_part(tool)}"
    return sanitize_tool_part(tool)


def parse_exposed_tool_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    parts = name.split("__")
    if len(parts) < 3:
        return None
    server = parts[1]
    tool = "__".join(parts[2:])
    return server, tool


def mcp_tool_to_openrouter(
    server: str,
    tool_name: str,
    description: str,
    input_schema: dict,
    *,
    use_prefix: bool = True,
) -> dict:
    exposed = exposed_tool_name(server, tool_name, use_prefix=use_prefix)
    return {
        "type": "function",
        "function": {
            "name": exposed,
            "description": f"[MCP:{server}] {description or tool_name}",
            "parameters": input_schema
            if input_schema
            else {"type": "object", "properties": {}},
        },
    }


def truncate_mcp_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = f"\n\n[... truncated {len(text) - max_chars} chars ...]"
    return text[: max_chars - len(marker)] + marker
