from agent.config import Config
from agent.models import WebSearchItem
from agent.sandbox.policy import SandboxMode
from tools.registry import dispatch_tool, get_tool_schemas, tool_requires_approval


def test_web_search_schema_when_enabled() -> None:
    config = Config(
        cwd=".",
        model="test",
        openrouter_api_key="x",
    )
    config.web_search.enabled = True
    names = [s["function"]["name"] for s in get_tool_schemas(None, config)]
    assert "web_search" in names


def test_web_search_schema_when_disabled() -> None:
    config = Config(cwd=".", model="test", openrouter_api_key="x")
    names = [s["function"]["name"] for s in get_tool_schemas(None, config)]
    assert "web_search" not in names


def test_web_search_requires_approval() -> None:
    assert tool_requires_approval("web_search", None, Config(cwd=".", model="t", openrouter_api_key="x"))


def test_web_search_disabled_dispatch() -> None:
    config = Config(cwd=".", model="test", openrouter_api_key="x")
    result = dispatch_tool("web_search", {"query": "pytest"}, config)
    assert "disabled" in result.text.lower()


def test_web_search_dispatch_mocked() -> None:
    from unittest.mock import patch

    from agent.settings import WebSearchSettings

    config = Config(cwd=".", model="test", openrouter_api_key="x")
    config.web_search = WebSearchSettings(enabled=True)
    with patch(
        "tools.registry.web_search",
        return_value=([], None),
    ):
        result = dispatch_tool("web_search", {"query": "python"}, config)
    assert result.web_search_item is not None
    assert isinstance(result.web_search_item, WebSearchItem)
