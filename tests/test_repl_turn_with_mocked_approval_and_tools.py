from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.events import EventEmitter
from agent.repl import ReplSession
from model.openrouter import CompletionResult


def test_repl_turn_streams_tools_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = Config.resolve(
        cwd=tmp_path,
        model="anthropic/claude-sonnet-4",
        auto_approve=True,
    )
    lines: list[str] = []

    session = ReplSession(
        config=config,
        store=__import__("agent.store", fromlist=["ThreadStore"]).ThreadStore(
            base_dir=tmp_path / "threads"
        ),
        print_fn=lines.append,
    )

    stream_results = [
        CompletionResult(
            "Checking",
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "arguments": '{"cmd":"echo hi"}',
                    },
                }
            ],
            "tool_calls",
            {"prompt_tokens": 400, "completion_tokens": 40},
        ),
        CompletionResult(
            "Finished.",
            [],
            "stop",
            {"prompt_tokens": 100, "completion_tokens": 10},
        ),
    ]

    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.stream_completion.side_effect = stream_results
        assert session.handle_line("run echo hi") is True

    joined = "\n".join(lines)
    assert "Finished." in joined
    assert "[done]" in joined
    assert "cost≈$" in joined


def test_repl_profile_commands() -> None:
    config = Config(cwd=Path("."), model="m", openrouter_api_key="k")
    lines: list[str] = []
    session = ReplSession(config=config, print_fn=lines.append)
    assert session.handle_line("/profile ci") is True
    assert session.handle_line("/model-profile deep") is True
    assert "ci" in lines[-2]
    assert "deep" in lines[-1]
