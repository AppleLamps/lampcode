from __future__ import annotations

from pathlib import Path

from agent.action_log import ActionLogHandler, format_action_log_line, project_action_log_path
from agent.events import AgentEvent
from agent.settings import ActionLogSettings


def test_format_tool_executing() -> None:
    line = format_action_log_line(
        AgentEvent(
            "tool.executing",
            data={"tool_name": "read_file", "arguments": {"path": "index.html"}},
        )
    )
    assert line == "tool executing: read_file index.html"


def test_format_tool_pending() -> None:
    line = format_action_log_line(
        AgentEvent(
            "tool.pending",
            thread_id="t1",
            turn_id="u1",
            data={"tool_name": "run_command", "arguments": {"cmd": "pytest -q"}},
        )
    )
    assert line is not None
    assert "run_command" in line
    assert "pytest" in line


def test_action_log_writes_project_mirror(tmp_path: Path) -> None:
    settings = ActionLogSettings(enabled=True, dir=str(tmp_path / "logs"), mirror_to_project=True)
    handler = ActionLogHandler(settings, project_cwd=tmp_path, model="test/model")
    handler.handle(AgentEvent("turn.started", thread_id="thread-1", turn_id="turn-1"))
    handler.handle(
        AgentEvent(
            "tool.pending",
            thread_id="thread-1",
            turn_id="turn-1",
            data={"tool_name": "read_file", "arguments": {"path": "foo.py"}},
        )
    )
    handler.handle(
        AgentEvent(
            "agent.delta",
            thread_id="thread-1",
            turn_id="turn-1",
            data={"text": "Done."},
        )
    )
    handler.handle(
        AgentEvent(
            "turn.completed",
            thread_id="thread-1",
            turn_id="turn-1",
            data={"status": "completed"},
        )
    )

    project_log = project_action_log_path(tmp_path)
    assert project_log.is_file()
    text = project_log.read_text(encoding="utf-8")
    assert "thread-1" in text
    assert "read_file" in text
    assert "assistant: Done." in text
    assert "turn completed" in text or "--- turn completed ---" in text

    thread_log = tmp_path / "logs" / "thread-1.log"
    assert thread_log.is_file()
