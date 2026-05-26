from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from agent.init_scaffold import ensure_workspace_ready
from cli.main import app

runner = CliRunner()


def test_bare_agent_launches_interactive_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    with patch("cli.main.launch_interactive_session") as launch:
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        launch.assert_called_once()


def test_agent_subcommand_skips_default_launch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch("cli.main.launch_interactive_session") as launch:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        launch.assert_not_called()


def test_ensure_workspace_ready_scaffolds_silently(tmp_path: Path) -> None:
    ensure_workspace_ready(tmp_path)
    assert (tmp_path / ".agent-cli" / "config.toml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    ensure_workspace_ready(tmp_path)
