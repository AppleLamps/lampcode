"""Modal overlay for request_user_input while a turn is running."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option


class UserInputOverlayScreen(ModalScreen[dict[str, str | None] | None]):
    """Collect an answer to a structured agent question."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    UserInputOverlayScreen {
        align: center middle;
    }
    #user_input_frame {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        border: tall #e3b341;
        background: #1c1400;
        padding: 1 2;
    }
    #user_input_question {
        height: auto;
        max-height: 6;
        margin-bottom: 1;
        color: #e3b341;
    }
    #user_input_options {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
        border: solid #484f58;
        background: #0d1117;
    }
    #user_input_answer {
        height: 3;
        min-height: 3;
        border: solid #58a6ff;
        background: #0d1117;
    }
    #user_input_hint {
        height: 1;
        color: #8b949e;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        *,
        question: str,
        options: list[str] | None = None,
        allow_free_text: bool = True,
        question_index: int = 1,
        question_total: int = 1,
    ) -> None:
        super().__init__()
        self._question = question
        self._options = list(options or [])
        self._allow_free_text = allow_free_text
        self._question_index = question_index
        self._question_total = question_total

    def compose(self) -> ComposeResult:
        with Vertical(id="user_input_frame"):
            yield Static(id="user_input_question")
            yield OptionList(id="user_input_options")
            yield TextArea(id="user_input_answer")
            yield Static(id="user_input_hint")

    def on_mount(self) -> None:
        title = "[bold]Agent asks[/bold]"
        if self._question_total > 1:
            title += f" [dim]({self._question_index}/{self._question_total})[/dim]"
        self.query_one("#user_input_question", Static).update(
            f"{title}\n{self._question}"
        )
        options = self.query_one("#user_input_options", OptionList)
        answer = self.query_one("#user_input_answer", TextArea)
        hint = self.query_one("#user_input_hint", Static)

        if self._options:
            options.clear_options()
            for idx, opt in enumerate(self._options):
                options.add_option(Option(opt, id=str(idx)))
            options.display = True
            hint.update(
                "[dim]Pick an option (click or number) · type custom answer below · Enter submit[/dim]"
                if self._allow_free_text
                else "[dim]Pick an option · Enter submit[/dim]"
            )
        else:
            options.display = False
            hint.update("[dim]Type your answer · Enter submit · Esc cancel[/dim]")

        if not self._allow_free_text and self._options:
            answer.display = False
        else:
            answer.display = True
            answer.placeholder = "Your answer…"
        answer.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "user_input_options":
            return
        try:
            idx = int(str(event.option_id))
            if 0 <= idx < len(self._options):
                self.query_one("#user_input_answer", TextArea).text = self._options[idx]
        except ValueError:
            pass

    def on_text_area_submitted(self, event: TextArea.Submitted) -> None:
        if event.text_area.id != "user_input_answer":
            return
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        raw = self.query_one("#user_input_answer", TextArea).text.strip()
        selected: str | None = None
        answer = raw

        if self._options:
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(self._options):
                    selected = self._options[idx]
                    answer = selected
            elif raw in self._options:
                selected = raw
                answer = raw
            elif not raw and self._options:
                self.notify("Choose an option or type an answer", severity="warning")
                return
            elif not self._allow_free_text and raw not in self._options:
                self.notify("Free text not allowed — pick a listed option", severity="warning")
                return

        if not answer:
            self.notify("Answer cannot be empty", severity="warning")
            return

        self.dismiss({"answer": answer, "selected_option": selected})
