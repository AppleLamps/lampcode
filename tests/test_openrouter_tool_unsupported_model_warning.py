from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agent.providers.openrouter import model_supports_tools, tool_support_warning
from cli.main import app

runner = CliRunner()


def test_denylist_model_not_supported() -> None:
    assert not model_supports_tools("openrouter/auto")
    assert tool_support_warning("openrouter/auto") is not None


def test_common_model_supported() -> None:
    assert model_supports_tools("anthropic/claude-sonnet-4")
    assert tool_support_warning("anthropic/claude-sonnet-4") is None
    assert tool_support_warning("minimax/minimax-m2.7") is None


def test_run_warns_on_unsupported_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")

    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        from model.openrouter import CompletionResult

        mock_client_cls.return_value.stream_completion.return_value = CompletionResult(
            "ok",
            [],
            "stop",
            None,
        )
        result = runner.invoke(
            app,
            [
                "run",
                "hello",
                "--cwd",
                str(tmp_path),
                "--model",
                "openrouter/auto",
                "--skip-git-check",
            ],
        )

    out = (result.stdout or "") + (result.stderr or "")
    assert "may not support tool calling" in out
