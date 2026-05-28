from __future__ import annotations

from typing import Any

from agent.mcp.adapter import parse_exposed_tool_name


def is_tool_allowed(
    tool_name: str,
    allowed_tools: list[str] | None,
    *,
    allow_mcp_servers: list[str] | None = None,
) -> bool:
    if allowed_tools is None:
        return True
    if tool_name in allowed_tools:
        return True
    if allow_mcp_servers and tool_name.startswith("mcp__"):
        parsed = parse_exposed_tool_name(tool_name)
        if parsed and parsed[0] in allow_mcp_servers:
            return True
    return False


def filter_tool_schemas(
    schemas: list[dict[str, Any]],
    allowed_tools: list[str] | None,
    *,
    allow_mcp_servers: list[str] | None = None,
) -> list[dict[str, Any]]:
    if allowed_tools is None and not allow_mcp_servers:
        return schemas
    return [
        s
        for s in schemas
        if is_tool_allowed(
            s.get("function", {}).get("name", ""),
            allowed_tools,
            allow_mcp_servers=allow_mcp_servers,
        )
    ]
