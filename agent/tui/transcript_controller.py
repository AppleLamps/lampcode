"""Incremental transcript sync for Static cell widgets."""

from __future__ import annotations

from textual.containers import Vertical, VerticalScroll

from agent.tui.cells.base import (
    AssistantMessageCell,
    PatchCell,
    PlanCell,
    ReasoningCell,
    ToolExecCell,
    ToolGroupCell,
)
from agent.tui.streaming_controller import AssistantStreamController
from agent.tui.transcript_pane import TranscriptPane, cell_dom_id
from agent.tui.view_model import TuiState


class TranscriptController:
    """Sync TuiState transcript to one Static widget per cell."""

    def __init__(self) -> None:
        self._stream = AssistantStreamController()
        self._live_text: str = ""
        self.follow_tail: bool = True

    def reset(self) -> None:
        self._stream.reset()
        self._live_text = ""

    def clear_live_stream_state(self) -> None:
        self._stream.reset()
        self._live_text = ""

    def rebuild_all(
        self,
        cells_col: Vertical,
        state: TuiState,
        *,
        scroll: VerticalScroll | None = None,
    ) -> None:
        """Full rebuild — session load, resize, expand toggle."""
        self.reset()
        for child in list(cells_col.children):
            child.remove()
        self.sync(cells_col, state, scroll=scroll)

    def sync(
        self,
        cells_col: Vertical,
        state: TuiState,
        *,
        scroll: VerticalScroll | None = None,
        force_scroll: bool = False,
    ) -> None:
        """Incremental sync: mount new cells, update in place, live assistant stream."""
        pane = TranscriptPane(cells_col, scroll)
        active_ids = {cell.cell_id for cell in state.transcript}
        pane.prune_orphans(active_ids)
        for cell in state.transcript:
            pane.mount_cell(cell)
        self._sync_live_stream(pane, state)
        pane.scroll_to_end(force=force_scroll or self.follow_tail, follow=self.follow_tail)

    def finalize_assistant_stream(
        self,
        cells_col: Vertical,
        state: TuiState,
        *,
        scroll: VerticalScroll | None = None,
    ) -> None:
        """Flush holdback into live stream before buffer → AssistantMessageCell."""
        if state.assistant_buffer:
            tail = self._stream.flush_remainder(state.assistant_buffer)
            self._live_text += tail
            pane = TranscriptPane(cells_col, scroll)
            if self._live_text:
                pane.set_live_stream(self._live_text)
        self._stream.reset()

    def commit_orphan_live_stream(self, state: TuiState) -> None:
        """Persist live-stream tail when the buffer was flushed at a tool boundary."""
        from agent.tui.view_model import _last_assistant_cell

        holdback = self._stream.flush_remainder(state.assistant_buffer)
        combined = (self._live_text + holdback).strip()
        self._live_text = ""
        self._stream.reset()
        if not combined:
            return

        last = _last_assistant_cell(state)
        if last:
            last_text = last.text.strip()
            if last_text == combined:
                return
            if combined.startswith(last_text) and len(combined) > len(last_text):
                last.text = combined
                return
            if last_text.startswith(combined):
                return

        state.transcript.append(AssistantMessageCell(text=combined, streaming=False))

    def _sync_live_stream(self, pane: TranscriptPane, state: TuiState) -> None:
        buffer = state.assistant_buffer
        if not buffer:
            pane.set_live_stream(None)
            self._stream.reset()
            self._live_text = ""
            return
        suffix = self._stream.absorb(buffer)
        self._live_text += suffix
        if self._live_text:
            pane.set_live_stream(self._live_text)

    def toggle_expand(self, state: TuiState, widget_dom_id: str) -> bool:
        for cell in state.transcript:
            if cell_dom_id(cell.cell_id) != widget_dom_id:
                continue
            if isinstance(
                cell, (ToolExecCell, PatchCell, PlanCell, ToolGroupCell, ReasoningCell)
            ):
                cell.expanded = not cell.expanded
                return True
        return False

    def toggle_last_expandable(self, state: TuiState) -> str | None:
        for cell in reversed(state.transcript):
            if isinstance(
                cell, (ToolExecCell, PatchCell, PlanCell, ToolGroupCell, ReasoningCell)
            ):
                cell.expanded = not cell.expanded
                return cell.cell_id
        return None
