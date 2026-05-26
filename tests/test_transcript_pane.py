"""Tests for VerticalScroll + Static transcript pane."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from agent.events import AgentEvent
from agent.tui.cells.base import PatchCell, ToolExecCell
from agent.tui.transcript_controller import TranscriptController
from agent.tui.transcript_pane import LIVE_STREAM_ID, cell_dom_id, compose_transcript_shell
from agent.tui.view_model import TuiState, apply_event_to_state


class TranscriptHarnessApp(App):
    """Minimal app hosting a transcript pane for Pilot tests."""

    def __init__(self, state: TuiState | None = None) -> None:
        super().__init__()
        self.harness_state = state or TuiState()
        self.controller = TranscriptController()

    def compose(self) -> ComposeResult:
        yield from compose_transcript_shell()

    def transcript_column(self) -> tuple[VerticalScroll, Vertical]:
        scroll = self.query_one("#transcript", VerticalScroll)
        cells = scroll.query_one("#transcript_cells", Vertical)
        return scroll, cells

    def sync_transcript(self, *, rebuild: bool = False) -> None:
        scroll, cells = self.transcript_column()
        if rebuild:
            self.controller.rebuild_all(cells, self.harness_state, scroll=scroll)
        else:
            self.controller.sync(cells, self.harness_state, scroll=scroll)


def _cell_widgets(cells: Vertical) -> list[Static]:
    return [
        child
        for child in cells.children
        if isinstance(child, Static) and child.id != LIVE_STREAM_ID
    ]


@pytest.mark.asyncio
async def test_tool_completed_updates_same_widget() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "run_command", "arguments": {"cmd": "echo hi"}},
        ),
    )
    cell_id = state.transcript[0].cell_id
    wid = cell_dom_id(cell_id)

    app = TranscriptHarnessApp(state)
    async with app.run_test() as pilot:
        app.sync_transcript()
        await pilot.pause()
        scroll, cells = app.transcript_column()
        assert len(_cell_widgets(cells)) == 1
        running = cells.query_one(f"#{wid}", Static)
        assert isinstance(state.transcript[0], ToolExecCell)
        assert state.transcript[0].status == "running"

        state = apply_event_to_state(
            state,
            AgentEvent(
                "tool.completed",
                thread_id="t",
                turn_id="u",
                data={"tool_name": "run_command", "status": "completed", "output": "hi"},
            ),
        )
        app.harness_state = state
        app.sync_transcript()
        await pilot.pause()
        assert len(_cell_widgets(cells)) == 1
        completed = cells.query_one(f"#{wid}", Static)
        assert completed is running
        assert state.transcript[0].status == "completed"
        assert "completed" in str(completed.render()).lower()


@pytest.mark.asyncio
async def test_patch_cell_single_widget_on_complete() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "apply_patch", "arguments": {}},
        ),
    )
    assert isinstance(state.transcript[-1], PatchCell)
    wid = cell_dom_id(state.transcript[-1].cell_id)

    app = TranscriptHarnessApp(state)
    async with app.run_test() as pilot:
        app.sync_transcript()
        await pilot.pause()
        _, cells = app.transcript_column()
        assert len(_cell_widgets(cells)) == 1

        state = apply_event_to_state(
            state,
            AgentEvent(
                "tool.completed",
                thread_id="t",
                turn_id="u",
                data={
                    "tool_name": "apply_patch",
                    "status": "completed",
                    "diff_preview": "+line",
                },
            ),
        )
        app.harness_state = state
        app.sync_transcript()
        await pilot.pause()
        assert len(_cell_widgets(cells)) == 1
        widget = cells.query_one(f"#{wid}", Static)
        assert "completed" in str(widget.render()).lower()


@pytest.mark.asyncio
async def test_live_stream_mount_and_clear() -> None:
    state = TuiState()
    app = TranscriptHarnessApp(state)
    async with app.run_test() as pilot:
        app.sync_transcript()
        await pilot.pause()
        _, cells = app.transcript_column()
        with pytest.raises(NoMatches):
            cells.query_one(f"#{LIVE_STREAM_ID}", Static)

        state = apply_event_to_state(
            state,
            AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hello"}),
        )
        app.harness_state = state
        app.sync_transcript()
        await pilot.pause()
        live = cells.query_one(f"#{LIVE_STREAM_ID}", Static)
        assert live is not None
        assert state.assistant_buffer == "Hello"

        state = apply_event_to_state(
            state,
            AgentEvent(
                "tool.pending",
                thread_id="t",
                turn_id="u",
                data={"tool_name": "read_file", "arguments": {"path": "a.py"}},
            ),
        )
        app.harness_state = state
        app.sync_transcript()
        await pilot.pause()
        with pytest.raises(NoMatches):
            cells.query_one(f"#{LIVE_STREAM_ID}", Static)
        assert isinstance(state.transcript[-1], ToolExecCell)


@pytest.mark.asyncio
async def test_rebuild_all_removes_stale_widgets() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "run_command", "arguments": {}},
        ),
    )
    app = TranscriptHarnessApp(state)
    async with app.run_test() as pilot:
        app.sync_transcript()
        await pilot.pause()
        _, cells = app.transcript_column()
        assert len(_cell_widgets(cells)) == 1
        state.transcript.clear()
        app.harness_state = state
        app.sync_transcript(rebuild=True)
        await pilot.pause()
        assert len(_cell_widgets(cells)) == 0
