from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Config
from agent.model_routing import (
    DEFAULT_ROUTING_RULES,
    explain_model_routing,
    load_model_routing,
    resolve_model_profile_from_task,
)
from agent.profiles import merge_layered_config
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def test_default_routing_rules() -> None:
    assert any(r.profile == "deep" for r in DEFAULT_ROUTING_RULES)


def test_resolve_fix_tests_to_deep() -> None:
    assert resolve_model_profile_from_task("fix failing pytest tests") == "deep"


def test_cli_model_profile_wins() -> None:
    assert resolve_model_profile_from_task("fix tests", cli_model_profile="fast") == "fast"


def test_summarize_routes_fast() -> None:
    assert resolve_model_profile_from_task("summarize this module") == "fast"


def test_explain_model_routing_matched_rule() -> None:
    msg = explain_model_routing("fix failing pytest tests")
    assert "deep" in msg
    assert "Matched" in msg


def test_load_model_routing_from_project(tmp_path: Path) -> None:
    cfg = tmp_path / ".agent-cli" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        """
[model_routing]
enabled = true
rules = [
  { match = "deploy|release", profile = "fast" },
]
""",
        encoding="utf-8",
    )
    routing = load_model_routing(project_path=cfg)
    assert routing.enabled
    assert routing.rules[0].profile == "fast"


def test_models_recommend_uses_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"

[model_routing]
enabled = true
rules = [
  { match = "fix test", profile = "deep" },
]
""",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["models", "recommend", "--task", "fix test failures", "--cwd", str(tmp_path)],
    )
    assert result.exit_code == 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "claude-sonnet-4" in out


def test_run_applies_routing_when_no_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
max_tool_rounds = 5
""",
        encoding="utf-8",
    )
    routed = resolve_model_profile_from_task("fix failing tests", cwd=tmp_path)
    assert routed == "deep"
    merged = merge_layered_config(tmp_path, cli_model_profile=routed)
    from agent.profiles import apply_merged_to_resolve_kwargs

    kwargs = apply_merged_to_resolve_kwargs(merged)
    cfg = Config.resolve(cwd=tmp_path, **kwargs)
    assert cfg.model == "anthropic/claude-sonnet-4"
