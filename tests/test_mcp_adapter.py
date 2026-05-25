from agent.mcp.adapter import (
    exposed_tool_name,
    mcp_tool_to_openrouter,
    parse_exposed_tool_name,
    sanitize_tool_part,
)


def test_sanitize_tool_part() -> None:
    assert sanitize_tool_part("read-file") == "read_file"


def test_exposed_tool_name() -> None:
    assert exposed_tool_name("filesystem", "read_file") == "mcp__filesystem__read_file"


def test_parse_exposed_tool_name() -> None:
    assert parse_exposed_tool_name("mcp__filesystem__read_file") == (
        "filesystem",
        "read_file",
    )


def test_mcp_tool_to_openrouter() -> None:
    schema = mcp_tool_to_openrouter(
        "filesystem",
        "read_file",
        "Read a file",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )
    assert schema["function"]["name"] == "mcp__filesystem__read_file"
    assert "Read a file" in schema["function"]["description"]
