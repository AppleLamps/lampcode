from agent.models import McpToolCallItem, SkillActivationItem, parse_item


def test_skill_activation_item() -> None:
    item = SkillActivationItem(skills=["pytest-fix"])
    parsed = parse_item(item.model_dump())
    assert parsed is not None
    assert parsed.skills == ["pytest-fix"]


def test_mcp_tool_call_item() -> None:
    item = McpToolCallItem(
        server="filesystem",
        tool="read_file",
        arguments={"path": "x"},
        status="completed",
        output="data",
    )
    parsed = parse_item(item.model_dump())
    assert parsed is not None
    assert parsed.server == "filesystem"


def test_unknown_item_returns_none() -> None:
    assert parse_item({"type": "futureItem", "id": "1"}) is None
