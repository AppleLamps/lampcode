"""VerticalScroll transcript pane with one Static widget per cell."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from agent.tui.cells import render_cell
from agent.tui.cells.message import assistant_message_visual
from agent.tui.cells.base import (
    PatchCell,
    PlanCell,
    ReasoningCell,
    ToolExecCell,
    ToolGroupCell,
    TranscriptCell,
)

LIVE_STREAM_ID = "live_stream"
_CELL_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def cell_dom_id(cell_id: str) -> str:
    safe = _CELL_ID_RE.sub("_", cell_id).strip("_")
    return f"cell-{safe or 'unknown'}"


def dom_id_to_cell_id(dom_id: str) -> str | None:
    if not dom_id.startswith("cell-"):
        return None
    return dom_id[5:]


def _cell_css_classes(cell: TranscriptCell) -> str:
    classes = ["transcript-cell"]
    if isinstance(
        cell,
        (ToolExecCell, PatchCell, PlanCell, ToolGroupCell, ReasoningCell),
    ):
        classes.append("expandable-cell")
    return " ".join(classes)


def compose_transcript_shell(
    *,
    transcript_id: str = "transcript",
    cells_id: str = "transcript_cells",
) -> ComposeResult:
    """Shared scroll + cells column layout for main chat and overlay."""
    with VerticalScroll(id=transcript_id, classes="transcript-scroll"):
        yield Vertical(id=cells_id)


class TranscriptPane:
    """Mount/update/prune Static children for transcript cells."""

    def __init__(
        self,
        cells_col: Vertical,
        scroll: VerticalScroll | None = None,
    ) -> None:
        self._cells = cells_col
        self._scroll = scroll

    def prune_orphans(self, active_cell_ids: set[str]) -> None:
        expected = {cell_dom_id(cid) for cid in active_cell_ids}
        for child in list(self._cells.children):
            wid = child.id
            if wid == LIVE_STREAM_ID:
                continue
            if wid and wid not in expected:
                child.remove()

    def has_cell_widget(self, cell_id: str) -> bool:
        try:
            self._cells.query_one(f"#{cell_dom_id(cell_id)}", Static)
            return True
        except NoMatches:
            return False

    def mount_cell(self, cell: TranscriptCell) -> Static:
        wid = cell_dom_id(cell.cell_id)
        markup = render_cell(cell)
        try:
            widget = self._cells.query_one(f"#{wid}", Static)
            widget.update(markup)
            return widget
        except NoMatches:
            widget = Static(markup, id=wid, classes=_cell_css_classes(cell))
            live = self._live_widget()
            if live is not None:
                self._cells.mount(widget, before=live)
            else:
                self._cells.mount(widget)
            return widget

    def set_live_stream(self, text: str | None) -> None:
        if not text:
            live = self._live_widget()
            if live is not None:
                live.remove()
            return
        visual = assistant_message_visual(text)
        live = self._live_widget()
        if live is not None:
            live.update(visual)
            return
        widget = Static(visual, id=LIVE_STREAM_ID, classes="transcript-cell live-stream")
        self._cells.mount(widget)

    def _live_widget(self) -> Static | None:
        try:
            return self._cells.query_one(f"#{LIVE_STREAM_ID}", Static)
        except NoMatches:
            return None

    def scroll_to_end(self, *, force: bool = False, follow: bool = False) -> None:
        """Scroll transcript to the latest content.

        follow=True keeps the view pinned during streaming (default for new sessions).
        force=True always scrolls (e.g. while a turn is running).
        """
        if self._scroll is None:
            return
        if not force and not follow and not self.is_near_bottom():
            return

        def _do_scroll() -> None:
            if self._scroll is None:
                return
            # scroll_end after layout so max_scroll_y includes new cells
            self._scroll.scroll_end(animate=False, immediate=True)
            live = self._live_widget()
            if live is not None:
                self._scroll.scroll_to_widget(live, animate=False)

        self._scroll.call_after_refresh(_do_scroll)

    def is_near_bottom(self, *, slack: int = 4) -> bool:
        if self._scroll is None:
            return True
        try:
            max_y = self._scroll.max_scroll_y
        except Exception:
            return True
        return self._scroll.scroll_y >= max(0, max_y - slack)
