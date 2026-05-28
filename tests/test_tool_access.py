from agent.tool_access import filter_tool_schemas, is_tool_allowed


def test_is_tool_allowed_builtin_only() -> None:
    assert is_tool_allowed("read_file", ["read_file"])
    assert not is_tool_allowed("run_command", ["read_file"])


def test_is_tool_allowed_mcp_lsp() -> None:
    assert is_tool_allowed(
        "mcp__lsp__lsp_references",
        ["read_file"],
        allow_mcp_servers=["lsp"],
    )
