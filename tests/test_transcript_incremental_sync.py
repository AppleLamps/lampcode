from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.events import AgentEvent
from agent.tui.cell_render_signature import cell_render_signature
from agent.tui.cells.base import ToolExecCell
from agent.tui.transcript_controller import TranscriptController
from agent.tui.view_model import TuiState, apply_event_to_state


def test_cell_render_signature_changes_when_tool_completes() -> None:
    cell = ToolExecCell(tool_name="run_command", status="running")
    before = cell_render_signature(cell)
    cell.status = "completed"
    cell.output = "ok"
    after = cell_render_signature(cell)
    assert before != after


def test_sync_cell_if_dirty_skips_unchanged() -> None:
    cell = ToolExecCell(tool_name="read_file", args_brief="a.py")
    ctrl = TranscriptController()
    pane = MagicMock()
    pane.has_cell_widget.return_value = True
    ctrl._rendered_signatures[cell.cell_id] = cell_render_signature(cell)

    ctrl._sync_cell_if_dirty(pane, cell)

    pane.mount_cell.assert_not_called()


def test_sync_cell_if_dirty_mounts_when_changed() -> None:
    cell = ToolExecCell(tool_name="read_file", status="running")
    ctrl = TranscriptController()
    pane = MagicMock()
    pane.has_cell_widget.return_value = True
    ctrl._rendered_signatures[cell.cell_id] = cell_render_signature(cell)

    cell.status = "completed"
    ctrl._sync_cell_if_dirty(pane, cell)

    pane.mount_cell.assert_called_once_with(cell)


def test_sync_stream_only_never_touches_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "read_file", "arguments": {"path": "a.py"}},
        ),
    )
    state = apply_event_to_state(
        state,
        AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hi"}),
    )

    ctrl = TranscriptController()
    cell_sync_calls: list[str] = []

    monkeypatch.setattr(
        ctrl,
        "_sync_cell_if_dirty",
        lambda pane, cell: cell_sync_calls.append(cell.cell_id),
    )
    monkeypatch.setattr(ctrl, "_sync_live_stream", lambda pane, st: None)

    class FakePane:
        def scroll_to_end(self, **kwargs):
            pass

    monkeypatch.setattr(
        "agent.tui.transcript_controller.TranscriptPane",
        lambda *a, **k: FakePane(),
    )

    ctrl.sync_stream_only(MagicMock(), state)
    assert cell_sync_calls == []
