"""Home and resume session picker orchestration."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Literal

from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from agent.models import Thread
from agent.threads_picker import thread_picker_label_summary, thread_resume_preview
from agent.tui.theme import home_menu_options
from agent.tui.view_model import filter_session_threads

if TYPE_CHECKING:
    from agent.tui.app import AgentTuiApp

ScreenMode = Literal["home", "chat", "resume"]


class HomeController:
    def __init__(self, app: AgentTuiApp) -> None:
        self._app = app

    def start_background_thread_index(self) -> None:
        def worker() -> None:
            try:
                threads = filter_session_threads(
                    self._app._store.list_thread_meta(), self._app._config.cwd
                )
                self._app.call_from_thread(self.on_threads_index_loaded, threads)
            except Exception as exc:
                self._app.call_from_thread(self.on_threads_index_failed, str(exc))

        self._app._index_worker = threading.Thread(target=worker, daemon=True)
        self._app._index_worker.start()

    def on_threads_index_loaded(self, threads: list[Thread]) -> None:
        self._app._session_threads = threads
        self._app._threads_loading = False
        if self._app._mode == "home":
            self.populate_home_menu()
        self._app._refresh_chrome()
        self.maybe_enter_pending_chat()

    def on_threads_index_failed(self, message: str) -> None:
        self._app._threads_loading = False
        self._app.query_one("#composer_footer", Static).update(
            f"[yellow]Could not list sessions:[/yellow] {message}"
        )

    def maybe_enter_pending_chat(self) -> None:
        if self._app._pending_chat == "none":
            return
        if self._app._pending_chat == "resume_last":
            if not self._app._session_threads:
                self._app._pending_chat = "none"
                return
            self.enter_chat_with_thread(self._app._session_threads[0].id)
            return
        if self._app._pending_chat == "thread_id" and self._app._requested_thread_id:
            self.enter_chat_with_thread(self._app._requested_thread_id)

    def enter_chat_with_thread(self, thread_id: str) -> None:
        self._app._pending_chat = "none"
        self._app.query_one("#composer_footer", Static).update(
            "[dim]Loading session…[/dim]"
        )

        def worker() -> None:
            try:
                thread = self._app._chat.resolve_thread(thread_id)
                self._app.call_from_thread(self.on_chat_thread_loaded, thread)
            except Exception as exc:
                self._app.call_from_thread(self.on_chat_thread_failed, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def on_chat_thread_loaded(self, thread: Thread) -> None:
        self._app._chat.load_thread(thread)
        self.set_mode("chat")
        self._app._chat.restore_composer_draft()
        self._app.query_one("#input", TextArea).focus()

    def on_chat_thread_failed(self, message: str) -> None:
        self._app._pending_chat = "none"
        self.set_mode("home")
        self._app.query_one("#composer_footer", Static).update(
            f"[yellow]Could not load session:[/yellow] {message}"
        )

    def populate_home_menu(self) -> None:
        menu = self._app.query_one("#home_menu", OptionList)
        menu.clear_options()
        for option_id, label in home_menu_options(
            has_sessions=bool(self._app._session_threads)
        ):
            shortcut = {
                "new": "Enter",
                "resume": "Ctrl+S",
                "quit": "Ctrl+Q",
            }.get(option_id, "")
            padding = max(1, 40 - len(label))
            menu.add_option(Option(f"{label}{' ' * padding}{shortcut}", id=option_id))

    def set_mode(self, mode: ScreenMode) -> None:
        self._app._mode = mode
        if mode == "home":
            self._apply_home_layout()
        elif mode == "resume":
            self._apply_resume_layout()
        else:
            self._app._chat.apply_chat_layout()

    def _apply_home_layout(self) -> None:
        home = self._app.query_one("#home_panel")
        transcript = self._app.query_one("#transcript")
        home_menu = self._app.query_one("#home_menu", OptionList)
        resume_menu = self._app.query_one("#resume_menu", OptionList)
        home.display = True
        transcript.display = False
        home_menu.display = True
        resume_menu.display = False
        self.populate_home_menu()
        self._app._refresh_chrome()

    def _apply_resume_layout(self) -> None:
        home = self._app.query_one("#home_panel")
        transcript = self._app.query_one("#transcript")
        home_menu = self._app.query_one("#home_menu", OptionList)
        resume_menu = self._app.query_one("#resume_menu", OptionList)
        home.display = True
        transcript.display = False
        home_menu.display = False
        resume_menu.display = True
        resume_menu.clear_options()
        for thread in self._app._session_threads:
            resume_menu.add_option(
                Option(thread_picker_label_summary(thread), id=thread.id)
            )
        resume_menu.add_option(Option("← Back", id="__back__"))
        if self._app._session_threads:
            self._app._resume_preview_text = (
                f"[dim]Preview[/dim]  {thread_resume_preview(self._app._session_threads[0])}"
            )
        self._app._refresh_chrome()

    def on_resume_menu_highlighted(self, option_id: str) -> None:
        if option_id in ("", "__back__"):
            self._app._resume_preview_text = ""
        else:
            try:
                thread = self._app._chat.resolve_thread(option_id)
                self._app._resume_preview_text = (
                    f"[dim]Preview[/dim]  {thread_resume_preview(thread)}"
                )
            except Exception:
                self._app._resume_preview_text = "[dim]Preview unavailable[/dim]"
        self._app._refresh_chrome()

    def handle_home_menu_selected(self, option_id: str) -> None:
        if option_id == "new":
            self._app.query_one("#input", TextArea).focus()
        elif option_id == "resume":
            self._app.action_resume_session()
        elif option_id == "quit":
            self._app.action_quit_app()

    def handle_resume_menu_selected(self, option_id: str) -> None:
        if option_id == "__back__":
            self.set_mode("home")
            return
        thread = self._app._chat.resolve_thread(option_id)
        self._app._chat.load_thread(thread)
        self.set_mode("chat")
        self._app._chat.restore_composer_draft()
        self._app.query_one("#input", TextArea).focus()

    def action_resume_session(self) -> None:
        if self._app._turn_running:
            self._app._turn.append_system("Wait for the current turn to finish.")
            return
        if self._app._threads_loading:
            self.set_mode("home")
            self._app.query_one("#composer_footer", Static).update(
                "[dim]Still loading sessions…[/dim]"
            )
            return
        if not self._app._session_threads:
            self._app._session_threads = filter_session_threads(
                self._app._store.list_thread_meta(), self._app._config.cwd
            )
        if not self._app._session_threads:
            self.set_mode("home")
            self._app.query_one("#composer_footer", Static).update(
                "No saved sessions in this folder yet — start typing below."
            )
            return
        self.set_mode("resume")

    def action_back_to_home(self) -> None:
        if self._app._mode == "resume":
            self.set_mode("home")

    def action_new_session(self) -> None:
        if self._app._turn_running:
            self._app._turn.append_system("Wait for the current turn to finish.")
            return
        self._app._thread = None
        from agent.tui.view_model import TuiState

        self._app._state = TuiState()
        self._app._transcript.reset()
        self._app._last_turn_cost = None
        from agent.tui.slash_commands import TuiSlashState

        self._app._slash = TuiSlashState()
        self._app._session_auto_approve = False
        self._app._composer_bindings = []
        inp = self._app.query_one("#input", TextArea)
        inp.clear()
        self.set_mode("home")
        inp.focus()
