"""TUI performance: stream coalescing, tool progress, read_file bounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.events import AgentEvent
from agent.tui.cells.message import assistant_message_visual
from agent.tui.stream_coalesce import STREAM_SYNC_INTERVAL_SEC
from agent.tui.view_model import TuiState, apply_event_to_state
from tools.files import DEFAULT_READ_LINE_LIMIT, read_file


def test_stream_coalesce_interval_is_thirty_fps_cap() -> None:
    assert STREAM_SYNC_INTERVAL_SEC == pytest.approx(0.033, abs=0.001)


def test_streaming_assistant_uses_plain_text_renderable() -> None:
    from rich.console import Group
    from rich.markdown import Markdown
    from rich.text import Text

    streaming = assistant_message_visual("**hello**", streaming=True)
    assert isinstance(streaming, Group)
    assert isinstance(streaming.renderables[1], Text)
    final = assistant_message_visual("**hello**", streaming=False)
    assert isinstance(final.renderables[1], Markdown)


def test_tool_executing_sets_progress_message() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.executing",
            thread_id="t1",
            turn_id="u1",
            data={
                "tool_name": "read_file",
                "arguments": {"path": "src/auth.py"},
            },
        ),
    )
    assert state.status_detail == "Reading auth.py…"
    from agent.tui.cells.base import WorkingCell

    assert any(isinstance(c, WorkingCell) for c in state.transcript)
    assert any(
        "Reading auth.py" in c.message
        for c in state.transcript
        if isinstance(c, WorkingCell)
    )


def test_read_file_default_line_limit(tmp_path: Path) -> None:
    sample = tmp_path / "big.txt"
    sample.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    out = read_file(tmp_path, "big.txt")
    assert "400|" in out
    assert "more lines omitted" in out


def test_read_file_path_outside_cwd_returns_error(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    out = read_file(tmp_path, str(outside))
    assert out.startswith("Error:")
    assert "escapes working directory" in out
