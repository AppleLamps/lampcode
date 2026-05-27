"""Modal approval overlay for tool/exec/patch prompts."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from agent.tui.cells.error import render_approval_banner_text


class ApprovalOverlayScreen(ModalScreen[str | None]):
    """Full-screen approval card with optional patch preview."""

    BINDINGS = [
        Binding("y", "choose_y", "Yes", show=True),
        Binding("n", "choose_n", "No", show=True),
        Binding("a", "choose_a", "All turn", show=True),
        Binding("A", "choose_A", "Session", show=True),
        Binding("escape", "choose_n", "Deny"),
    ]

    DEFAULT_CSS = """
    ApprovalOverlayScreen {
        align: center middle;
    }
    #approval_frame {
        width: 92%;
        max-width: 110;
        height: 85%;
        border: tall #e3b341;
        background: #1c1400;
        padding: 1 2;
    }
    #approval_title {
        height: 1;
        color: #e3b341;
        text-style: bold;
    }
    #approval_body {
        height: 1fr;
        border: solid #484f58;
        background: #0d1117;
        padding: 1;
    }
    #approval_keys {
        height: 1;
        margin-top: 1;
        color: #8b949e;
    }
    """

    def __init__(
        self,
        *,
        summary: str,
        diff_preview: str | None = None,
        tool_name: str | None = None,
        source_path: str | None = None,
    ) -> None:
        super().__init__()
        self._summary = summary
        self._diff_preview = diff_preview
        self._tool_name = tool_name
        self._source_path = source_path

    def compose(self) -> ComposeResult:
        with Vertical(id="approval_frame"):
            yield Static("Approve tool request", id="approval_title")
            with VerticalScroll(id="approval_body"):
                yield Static(id="approval_content")
            yield Static(id="approval_keys")

    def on_mount(self) -> None:
        body = render_approval_banner_text(
            self._summary,
            diff_preview=self._diff_preview,
            tool_name=self._tool_name,
            source_path=self._source_path,
        )
        self.query_one("#approval_content", Static).update(body)
        self.query_one("#approval_keys", Static).update(
            "[dim]y[/dim] yes  [dim]n[/dim] no  [dim]a[/dim] all this turn  "
            "[dim]A[/dim] all session  [dim]Esc[/dim] deny"
        )

    def action_choose_y(self) -> None:
        self.dismiss("y")

    def action_choose_n(self) -> None:
        self.dismiss("n")

    def action_choose_a(self) -> None:
        self.dismiss("a")

    def action_choose_A(self) -> None:
        self.dismiss("A")
