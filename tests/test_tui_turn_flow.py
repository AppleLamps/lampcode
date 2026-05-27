"""Textual pilot tests for multi-event turn flows."""

from __future__ import annotations

import pytest
from textual.css.query import NoMatches
from textual.widgets import Static

from agent.events import AgentEvent
from agent.tui.cells.base import ToolExecCell, TurnSummaryCell
from agent.tui.transcript_pane import LIVE_STREAM_ID
from agent.tui.view_model import TuiState, apply_event_to_state
from tests.test_transcript_pane import TranscriptHarnessApp, _cell_widgets


def _apply(app: TranscriptHarnessApp, state: TuiState, event: AgentEvent) -> TuiState:
    if event.type == "tool.pending":
        scroll, cells = app.transcript_column()
        app.controller.finalize_assistant_stream(cells, state, scroll=scroll)
    state = apply_event_to_state(state, event)
    if event.type == "turn.completed":
        app.controller.commit_orphan_live_stream(state)
        app.controller.clear_live_stream_state()
    app.harness_state = state
    scroll, cells = app.transcript_column()
    if event.type == "agent.delta":
        app.controller.sync_stream_only(cells, state, scroll=scroll)
    else:
        app.controller.sync(cells, state, scroll=scroll)
    return state


@pytest.mark.asyncio
async def test_turn_flow_mounts_tool_and_summary() -> None:
    state = TuiState()
    app = TranscriptHarnessApp(state)
    async with app.run_test(size=(100, 32)) as pilot:
        state = _apply(
            app,
            state,
            AgentEvent("turn.started", thread_id="t", turn_id="u", data={}),
        )
        state = _apply(
            app,
            state,
            AgentEvent(
                "agent.delta",
                thread_id="t",
                turn_id="u",
                data={"text": "Running tests.\n"},
            ),
        )
        state = _apply(
            app,
            state,
            AgentEvent(
                "tool.pending",
                thread_id="t",
                turn_id="u",
                data={"tool_name": "run_command", "arguments": {"cmd": "pytest -q"}},
            ),
        )
        await pilot.pause()
        _, cells = app.transcript_column()
        assert len(_cell_widgets(cells)) >= 1
        assert isinstance(state.transcript[-1], ToolExecCell)

        state = _apply(
            app,
            state,
            AgentEvent(
                "tool.completed",
                thread_id="t",
                turn_id="u",
                data={
                    "tool_name": "run_command",
                    "status": "completed",
                    "output": "1 passed",
                    "exit_code": 0,
                },
            ),
        )
        state = _apply(
            app,
            state,
            AgentEvent(
                "agent.delta",
                thread_id="t",
                turn_id="u",
                data={"text": "All green."},
            ),
        )
        state = _apply(
            app,
            state,
            AgentEvent(
                "turn.completed",
                thread_id="t",
                turn_id="u",
                data={"status": "completed"},
            ),
        )
        await pilot.pause()
        _, cells = app.transcript_column()
        assert any(isinstance(c, TurnSummaryCell) for c in state.transcript)
        assert len(_cell_widgets(cells)) >= 2
        with pytest.raises(NoMatches):
            cells.query_one(f"#{LIVE_STREAM_ID}", Static)


@pytest.mark.asyncio
async def test_delta_stream_only_skips_full_cell_resync() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "read_file", "arguments": {"path": "x.py"}},
        ),
    )
    app = TranscriptHarnessApp(state)
    async with app.run_test() as pilot:
        app.sync_transcript()
        await pilot.pause()
        _, cells = app.transcript_column()
        before = len(_cell_widgets(cells))

        for _ in range(5):
            state = apply_event_to_state(
                state,
                AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "."}),
            )
            app.harness_state = state
            scroll, cells_col = app.transcript_column()
            app.controller.sync_stream_only(cells_col, state, scroll=scroll)
        await pilot.pause()
        assert len(_cell_widgets(cells)) == before
