from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Config
from agent.profiles import merge_layered_config


def test_tui_resolves_model_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
max_tool_rounds = 40
reasoning_effort = "high"
""",
        encoding="utf-8",
    )
    merged = merge_layered_config(tmp_path, cli_model_profile="deep")
    kwargs = __import__(
        "agent.profiles", fromlist=["apply_merged_to_resolve_kwargs"]
    ).apply_merged_to_resolve_kwargs(merged)
    cfg = Config.resolve(cwd=tmp_path, **kwargs)
    assert cfg.model == "anthropic/claude-sonnet-4"
    assert cfg.max_rounds == 40


def test_tui_launch_passes_profile_kwargs() -> None:
    import inspect

    from agent.tui.runner import launch_tui

    sig = inspect.signature(launch_tui)
    assert "profile" in sig.parameters
    assert "model_profile" in sig.parameters
