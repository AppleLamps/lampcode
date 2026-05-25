from pathlib import Path

from agent.config import Config
from tools.registry import TOOL_REGISTRY, dispatch_tool


def test_registry_has_four_tools() -> None:
    assert set(TOOL_REGISTRY.keys()) == {
        "run_command",
        "read_file",
        "write_file",
        "search_repo",
    }


def test_read_file_dispatch(tmp_path: Path) -> None:
    sample = tmp_path / "hello.txt"
    sample.write_text("line1\nline2\n")
    config = Config(cwd=tmp_path, model="test", openrouter_api_key="x")

    result, item = dispatch_tool("read_file", {"path": "hello.txt"}, config)
    assert "line1" in result
    assert item is None


def test_write_file_dispatch(tmp_path: Path) -> None:
    config = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    result, item = dispatch_tool(
        "write_file", {"path": "out.txt", "content": "hello\n"}, config
    )
    assert "Successfully wrote" in result
    assert item is not None
    assert (tmp_path / "out.txt").read_text() == "hello\n"
