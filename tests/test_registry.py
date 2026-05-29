from pathlib import Path

from agent.config import Config
from tools.registry import TOOL_REGISTRY, dispatch_tool


def test_registry_has_core_tools() -> None:
    assert set(TOOL_REGISTRY.keys()) >= {
        "run_command",
        "read_file",
        "write_file",
        "apply_patch",
        "search_repo",
        "file_outline",
        "go_to_definition",
        "find_references",
        "file_imports",
        "request_user_input",
        "request_permissions",
    }


def test_read_file_dispatch(tmp_path: Path) -> None:
    sample = tmp_path / "hello.txt"
    sample.write_text("line1\nline2\n")
    config = Config(cwd=tmp_path, model="test", openrouter_api_key="x")

    result = dispatch_tool("read_file", {"path": "hello.txt"}, config)
    assert "line1" in result.text
    assert result.command_item is None
    assert not result.file_items


def test_read_file_respects_explicit_limit(tmp_path: Path) -> None:
    sample = tmp_path / "hello.txt"
    sample.write_text("line1\nline2\nline3\n")
    config = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    result = dispatch_tool(
        "read_file", {"path": "hello.txt", "limit": 1}, config
    )
    assert "line1" in result.text
    assert "line2" not in result.text


def test_write_file_dispatch(tmp_path: Path) -> None:
    config = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    result = dispatch_tool(
        "write_file", {"path": "out.txt", "content": "hello\n"}, config
    )
    assert "Successfully wrote" in result.text
    assert len(result.file_items) == 1
    assert (tmp_path / "out.txt").read_text() == "hello\n"
