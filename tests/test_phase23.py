from __future__ import annotations

import json
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent.compaction import compact_thread_if_needed
from agent.config import Config
from agent.context import build_thread_messages
from agent.execution.shell_session import ShellSession, pty_support_status
from agent.models import AgentMessageItem, Thread, Turn, UserMessageItem
from agent.settings import CompactionSettings, ShellSettings
from agent.store import ThreadStore
from cli.main import app

runner = CliRunner()

TASK_MARKER = "TASK_MARKER=phase23-compaction-resume"


def test_shell_session_persistent_env_survives(tmp_path: Path) -> None:
    settings = ShellSettings(enabled=True, persistent=True)
    sess = ShellSession("phase23", tmp_path, settings)
    try:
        if platform.system() == "Windows":
            set_result = sess.run("set AGENT_MARK=phase23")
            assert set_result.meta["persistent"] is True
            assert set_result.meta["backend"] == "pipes"
            echo_result = sess.run("echo %AGENT_MARK%")
        else:
            set_result = sess.run("export AGENT_MARK=phase23")
            assert set_result.meta["persistent"] is True
            echo_result = sess.run("echo $AGENT_MARK")
        assert echo_result.meta["persistent"] is True
        assert "phase23" in echo_result.output
    finally:
        sess.close()


def test_pty_status_reports_persistent_pipes_on_windows() -> None:
    status = pty_support_status()
    assert status["available"] is True
    if platform.system() == "Windows":
        assert status["backend"] in ("pipes", "conpty")
        assert status["conpty_available"] == (status["backend"] == "conpty")


def test_compaction_preserves_task_in_rebuilt_messages(tmp_path: Path) -> None:
    thread = Thread(id="t-compact", cwd=str(tmp_path), model="test")
    thread.turns.append(
        Turn(
            status="completed",
            items=[
                UserMessageItem(text=f"Fix calc.add bug. {TASK_MARKER}"),
                AgentMessageItem(text="Investigating failing pytest for calc.add."),
            ],
        )
    )
    for i in range(3):
        thread.turns.append(
            Turn(
                status="completed",
                items=[
                    UserMessageItem(text=f"follow-up question {i}"),
                    AgentMessageItem(text=f"answer {i}" * 400),
                ],
            )
        )

    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=100,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.1, keep_recent_turns=2),
    )
    client = MagicMock()
    client.complete.return_value = (
        f"User asked to fix calc.add. Preserve {TASK_MARKER} while continuing pytest work."
    )

    result = compact_thread_if_needed(thread, config, store, client)
    assert result.performed

    messages = build_thread_messages(thread)
    joined = "\n".join(
        msg["content"] for msg in messages if isinstance(msg.get("content"), str)
    )
    assert TASK_MARKER in joined
    assert "follow-up question 2" in joined
    assert "follow-up question 0" not in joined


def test_review_json_cli_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")

    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [
            (0, " calc.py | 1 +-", ""),
            (0, "- return a - b\n+ return a + b", ""),
            (0, " M calc.py", ""),
        ]
        with patch("agent.loop.OpenRouterClient") as mock_client:
            from model.openrouter import CompletionResult

            mock_client.return_value.stream_completion.return_value = CompletionResult(
                """## Summary
Subtract bug in add().

## Findings
- [critical] Wrong operator - uses subtraction (calc.py:2)

## Suggested fixes
- Use addition in add()

## Test gaps
- pytest for add(2, 3) == 5
""",
                [],
                "stop",
                {"prompt_tokens": 10, "completion_tokens": 20},
            )
            result = runner.invoke(
                app,
                [
                    "review",
                    "--uncommitted",
                    "--cwd",
                    str(tmp_path),
                    "--auto-approve",
                    "--skip-git-check",
                    "--json",
                ],
            )

    assert result.exit_code == 0, result.output
    assert "thread.started" in result.output
    assert '"type": "run.result"' in result.output
    assert '"severity_counts"' in result.output

    report_json = json.loads(result.output[result.output.rfind('{\n  "schema_version"'):])
    assert report_json["severity_counts"]["critical"] == 1
    assert report_json["findings"][0]["title"] == "Wrong operator"
    assert report_json["findings"][0]["severity"] == "critical"
