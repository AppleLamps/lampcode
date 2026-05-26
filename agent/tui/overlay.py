"""Full-session transcript overlay (Ctrl+T)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from agent.tui.cells.base import (
    PatchCell,
    PlanCell,
    ReasoningCell,
    ToolExecCell,
    ToolGroupCell,
    TranscriptCell,
)
from agent.tui.transcript_controller import TranscriptController
from agent.tui.transcript_pane import compose_transcript_shell
from agent.tui.view_model import TuiState


def _expandable_count(cells: list[TranscriptCell]) -> int:
    n = 0
    for cell in cells:
        if isinstance(cell, (ToolExecCell, PatchCell, PlanCell, ToolGroupCell, ReasoningCell)):
            n += 1
    return n


class TranscriptOverlayScreen(ModalScreen):
    """Scrollable full transcript with search and navigation."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("ctrl+t", "dismiss", "Close"),
        Binding("e", "toggle_expand", "Expand"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
    ]

    DEFAULT_CSS = """
    TranscriptOverlayScreen {
        align: center middle;
    }
    #overlay_frame {
        width: 96%;
        height: 92%;
        border: tall #30363d;
        background: #0a0a0a;
        padding: 0 1 1 1;
    }
    #overlay_header {
        dock: top;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #e6edf3;
    }
    #overlay_footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #6e7681;
    }
    #overlay_transcript {
        height: 1fr;
        border: none;
        background: #0a0a0a;
        padding: 1 2;
    }
    .transcript-cell.expandable-cell {
        pointer: pointer;
    }
    .transcript-cell.expandable-cell:hover {
        background: #161b22;
    }
    """

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state
        self._transcript = TranscriptController()

    def compose(self) -> ComposeResult:
        with Vertical(id="overlay_frame"):
            yield Static(id="overlay_header")
            yield from compose_transcript_shell(
                transcript_id="overlay_transcript",
                cells_id="overlay_transcript_cells",
            )
            yield Static(id="overlay_footer")

    def on_mount(self) -> None:
        self._refresh_header_footer()
        scroll = self.query_one("#overlay_transcript", VerticalScroll)
        cells = scroll.query_one("#overlay_transcript_cells", Vertical)
        self._transcript.rebuild_all(cells, self._state, scroll=scroll)

    def _refresh_header_footer(self) -> None:
        n_cells = len(self._state.transcript)
        n_expand = _expandable_count(self._state.transcript)
        self.query_one("#overlay_header", Static).update(
            f"[bold]Transcript[/bold]  [dim]· {n_cells} cells · {n_expand} expandable[/dim]"
        )
        self.query_one("#overlay_footer", Static).update(
            "[dim]Esc close · e expand cell · g top · G bottom · click panel to expand[/dim]"
        )

    def _overlay_targets(self) -> tuple[VerticalScroll, Vertical]:
        scroll = self.query_one("#overlay_transcript", VerticalScroll)
        cells = scroll.query_one("#overlay_transcript_cells", Vertical)
        return scroll, cells

    def _sync_overlay(self) -> None:
        scroll, cells = self._overlay_targets()
        self._transcript.sync(cells, self._state, scroll=scroll)
        self._refresh_header_footer()

    def action_dismiss(self) -> None:
        self.dismiss()

    def action_toggle_expand(self) -> None:
        if self._transcript.toggle_last_expandable(self._state):
            self._sync_overlay()

    def action_scroll_top(self) -> None:
        scroll = self.query_one("#overlay_transcript", VerticalScroll)
        scroll.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        scroll = self.query_one("#overlay_transcript", VerticalScroll)
        scroll.scroll_end(animate=False)

    def on_click(self, event) -> None:
        widget_id = str(event.widget.id or "")
        if not widget_id.startswith("cell-"):
            return
        if self._transcript.toggle_expand(self._state, widget_id):
            self._sync_overlay()
