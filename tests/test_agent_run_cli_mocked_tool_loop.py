from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agent.models import Thread, new_id
from cli.main import app
from model.openrouter import CompletionResult

runner = CliRunner()


def _cli_out(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


def test_agent_run_cli_mocked_tool_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    (tmp_path / "calc.py").write_text(
        "def add(a,b):\n    return a-b\n",
        encoding="utf-8",
    )

    stream_results = [
        CompletionResult(
            "",
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "calc.py", "content": "def add(a,b):\n    return a+b\n"}
                        ),
                    },
                }
            ],
            "tool_calls",
            {"prompt_tokens": 1000, "completion_tokens": 100},
        ),
        CompletionResult(
            "Done.",
            [],
            "stop",
            {"prompt_tokens": 100, "completion_tokens": 20},
        ),
    ]

    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.stream_completion.side_effect = stream_results
        result = runner.invoke(
            app,
            [
                "run",
                "fix the add function",
                "--cwd",
                str(tmp_path),
                "--auto-approve",
                "--skip-git-check",
            ],
        )

    assert result.exit_code == 0, _cli_out(result)
    out = _cli_out(result)
    assert "[tool]" in out or "Done." in out
    assert "[done]" in out
    assert "cost≈$" in out
    assert "return a+b" in (tmp_path / "calc.py").read_text(encoding="utf-8")


def test_agent_run_auto_routes_model_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
max_tool_rounds = 5

[model_profiles.fast]
model = "google/gemini-flash"
max_tool_rounds = 5
""",
        encoding="utf-8",
    )

    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.stream_completion.return_value = CompletionResult(
            "ok",
            [],
            "stop",
            {"prompt_tokens": 10, "completion_tokens": 5},
        )
        result = runner.invoke(
            app,
            ["run", "fix failing pytest tests", "--cwd", str(tmp_path), "--skip-git-check"],
        )

    assert result.exit_code == 0
    assert "Auto-routed model profile" in _cli_out(result) or "deep" in _cli_out(result)
