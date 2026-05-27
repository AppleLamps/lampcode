"""Textual TUI entry — thin shell delegating to mode controllers."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import OptionList, Static, TextArea

from agent.cancel import CancelToken
from agent.config import Config
from agent.memories import SuggestQueue
from agent.models import Thread
from agent.profiles import (
    apply_merged_to_resolve_kwargs,
    merge_layered_config,
)
from agent.recording.store import RunStore
from agent.store import ThreadStore
from agent.tui.composer import ComposerTextArea
from agent.tui.controllers import ChatController, HomeController, TurnController
from agent.tui.input_history import InputHistory
from agent.tui.mention_popup import MentionBinding, MentionCandidate
from agent.tui.messages import AgentEventMessage, WorkerErrorMessage, WorkerFinishedMessage
from agent.tui.slash_commands import TuiSlashState
from agent.tui.status_row import StatusRow
from agent.tui.statusline import load_tui_settings
from agent.tui.theme import GROK_CSS
from agent.tui.transcript_controller import TranscriptController
from agent.tui.transcript_pane import compose_transcript_shell
from agent.tui.transcript_reflow import TranscriptReflowState
from agent.tui.view_model import TuiState
from approval.gate import set_approval_input


class AgentTuiApp(App):
    TITLE = "agent"
    CSS = GROK_CSS

    BINDINGS = [
        Binding("ctrl+s", "resume_session", "Resume", show=False),
        Binding("ctrl+w", "new_session", "New", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
        Binding("ctrl+c", "cancel_turn", "Cancel", show=False),
        Binding("ctrl+t", "transcript_overlay", "Transcript", show=False),
        Binding("escape", "back_to_home", "Back", show=False),
        Binding("e", "toggle_expand", "Expand", show=False),
        Binding("m", "why_model", "Why model", show=False),
        Binding("ctrl+r", "reverse_search", "History", show=False),
    ]

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        thread_id: str | None = None,
        resume_last: bool = False,
        profile: str | None = None,
        model_profile: str | None = None,
        config: Config | None = None,
    ) -> None:
        super().__init__()
        resolved_cwd = cwd
        if resolved_cwd is None and config is not None:
            resolved_cwd = config.cwd
        resolved_cwd = Path(resolved_cwd or Path.cwd()).resolve()
        merged = merge_layered_config(
            resolved_cwd,
            cli_profile=profile,
            cli_model_profile=model_profile,
        )
        if config is not None:
            self._config = config
        else:
            kwargs = apply_merged_to_resolve_kwargs(merged)
            self._config = Config.resolve(cwd=resolved_cwd, **kwargs)
            self._config.require_api_key()
        self._profile = profile or merged.get("profile", "")
        self._model_profile = model_profile or merged.get("model_profile", "")
        self._last_turn_cost: float | None = None
        from agent.tui.theme import app_version

        self._app_version = app_version()
        self._store = ThreadStore()
        self._run_store = RunStore()
        self._thread: Thread | None = None
        self._requested_thread_id = thread_id
        self._resume_last = resume_last
        self._state = TuiState()
        self._transcript = TranscriptController()
        self._transcript.follow_tail = True
        self._status_row: StatusRow | None = None
        self._cancel = CancelToken()
        self._worker = None
        self._index_worker = None
        self._approval_queue: queue.Queue[str] = queue.Queue()
        self._user_input_response_queue: queue.Queue[dict[str, str | None]] = (
            queue.Queue()
        )
        self._event_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._session_auto_approve = False
        self._turn_running = False
        self._mode: Literal["home", "chat", "resume"] = "home"
        self._session_threads: list[Thread] = []
        self._slash = TuiSlashState()
        self._reflow = TranscriptReflowState()
        self._reflow_timer = None
        self._mention_candidates: list[MentionCandidate] = []
        self._mention_highlight: int = 0
        self._approval_overlay_open = False
        self._threads_loading = True
        self._pending_chat: Literal["none", "thread_id", "resume_last"] = "none"
        self._tui_settings = load_tui_settings(self._config.config_path)
        self._input_history = InputHistory(InputHistory.history_path(self._config.cwd))
        self._input_history.load()
        self._reverse_search_active = False
        self._reverse_search_query = ""
        self._reverse_search_match = ""
        self._reverse_search_saved_draft = ""
        self._resume_preview_text = ""
        self._composer_bindings: list[MentionBinding] = []
        self._composer_draft_timer = None
        if thread_id:
            self._pending_chat = "thread_id"
        elif resume_last:
            self._pending_chat = "resume_last"

        self._home = HomeController(self)
        self._chat = ChatController(self)
        self._turn = TurnController(self)

    # --- Config helpers (shared by controllers) ---

    def _slash_has_overrides(self) -> bool:
        return bool(
            self._slash.model_override
            or self._slash.profile_override
            or self._slash.model_profile_override
        )

    def _resolve_config(self) -> Config:
        if not self._slash_has_overrides():
            return self._config
        merged = merge_layered_config(
            self._config.cwd,
            cli_profile=self._slash.profile_override or self._profile or None,
            cli_model_profile=self._slash.model_profile_override
            or self._model_profile
            or None,
            cli_model=self._slash.model_override,
        )
        kwargs = apply_merged_to_resolve_kwargs(merged)
        return Config.resolve(cwd=self._config.cwd, **kwargs)

    def _active_model_profile_name(self) -> str | None:
        if self._slash.model_profile_override:
            return self._slash.model_profile_override
        merged = merge_layered_config(
            self._config.cwd,
            cli_profile=self._slash.profile_override or self._profile or None,
            cli_model_profile=self._model_profile or None,
            cli_model=self._slash.model_override,
        )
        name = merged.get("model_profile")
        return str(name) if name else None

    def _active_model_short(self) -> str:
        model = self._config.model
        if self._slash.model_override:
            model = self._slash.model_override
        return model.split("/")[-1]

    def _memories_pending_count(self) -> int:
        if not self._config.memories.enabled:
            return 0
        try:
            return SuggestQueue(self._config.cwd).pending_count()
        except OSError:
            return 0

    def _refresh_chrome(self, *, include_context: bool | None = None) -> None:
        self._chat.refresh_chrome(include_context=include_context)

    # --- Layout ---

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Vertical(id="app_shell"):
            with Vertical(id="main"):
                with Vertical(id="home_panel"):
                    with Vertical(id="home_stack"):
                        from agent.tui.theme import AGENT_LOGO

                        yield Static(AGENT_LOGO, id="logo")
                        yield OptionList(id="home_menu")
                    yield OptionList(id="resume_menu")
                yield from compose_transcript_shell()
            yield Static(id="approval_banner")
            with Vertical(id="bottom_chrome"):
                yield Static(id="status_row")
                yield Static(id="resume_preview")
                yield OptionList(id="mention_popup")
                yield Static(id="composer_meta")
                with Horizontal(id="composer_row"):
                    yield Static(id="mode_badge")
                    yield ComposerTextArea(id="input")
                yield Static(id="composer_footer")

    def on_mount(self) -> None:
        self._status_row = StatusRow(
            self.query_one("#status_row", Static),
            reduced_motion=self._tui_settings.reduced_motion,
        )
        self.query_one("#status_row", Static).display = False
        self.query_one("#approval_banner", Static).display = False
        self.query_one("#resume_preview", Static).display = False
        self.query_one("#mention_popup", OptionList).display = False

        self._home.set_mode("home")
        self._refresh_chrome()
        self.query_one("#input", TextArea).focus()
        self._home.start_background_thread_index()

    def on_unmount(self) -> None:
        set_approval_input(None)

    def on_resize(self) -> None:
        self._chat.on_resize()

    # --- Home / resume (delegated) ---

    def _set_mode(self, mode: Literal["home", "chat", "resume"]) -> None:
        self._home.set_mode(mode)

    def action_resume_session(self) -> None:
        self._home.action_resume_session()

    def action_back_to_home(self) -> None:
        self._home.action_back_to_home()

    def action_new_session(self) -> None:
        self._home.action_new_session()

    # --- Chat UI events (delegated) ---

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._chat.on_mouse_scroll_up(event)

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self._chat.on_mouse_scroll_down(event)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "resume_menu":
            return
        self._home.on_resume_menu_highlighted(str(event.option.id or ""))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = str(event.option_id or "")
        widget_id = event.option_list.id

        if widget_id == "mention_popup":
            try:
                self._chat.apply_mention_candidate(int(option_id))
            except ValueError:
                pass
            return

        if widget_id == "home_menu":
            self._home.handle_home_menu_selected(option_id)
            return

        if widget_id == "resume_menu":
            self._home.handle_resume_menu_selected(option_id)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "input":
            return
        self._chat.on_text_area_changed()

    def on_text_area_submitted(self, event: TextArea.Submitted) -> None:
        self._turn.submit_input(event.text_area.text)

    def _expand_file_mention(self, text: str) -> str | None:
        return self._chat.expand_file_mention(text)

    def on_click(self, event) -> None:
        if self._mode != "chat":
            return
        widget_id = str(event.widget.id or "")
        if widget_id.startswith("cell-"):
            self._chat.on_cell_click(widget_id)

    def action_transcript_overlay(self) -> None:
        self._chat.action_transcript_overlay()

    def action_toggle_expand(self) -> None:
        self._chat.action_toggle_expand()

    def action_reverse_search(self) -> None:
        self._chat.action_reverse_search()

    def action_reverse_search_cancel(self) -> None:
        self._chat.action_reverse_search_cancel()

    def action_reverse_search_accept(self) -> None:
        self._chat.action_reverse_search_accept()

    # --- Turn lifecycle (delegated) ---

    def _submit_input(self, raw_text: str) -> None:
        self._turn.submit_input(raw_text)

    def on_agent_event_message(self, _message: AgentEventMessage) -> None:
        self._turn.drain_event_queue()

    def on_worker_finished_message(self, _message: WorkerFinishedMessage) -> None:
        self._turn.turn_finished()

    def on_worker_error_message(self, message: WorkerErrorMessage) -> None:
        self._turn.on_worker_error(message.error)

    def _watch_turn_worker(self) -> None:
        self._turn.watch_turn_worker()

    def action_cancel_turn(self) -> None:
        self._turn.action_cancel_turn()

    def action_why_model(self) -> None:
        self._turn.action_why_model()

    def action_quit_app(self) -> None:
        if self._turn_running:
            self._turn.append_system("Turn running — cancel with Ctrl+C first.")
            return
        self.exit()

    # --- Back-compat aliases for tests and internal callers ---

    def _sync_transcript(self, *, rebuild: bool = False, stream_only: bool = False) -> None:
        self._chat.sync_transcript(rebuild=rebuild, stream_only=stream_only)

    def _load_thread(self, thread: Thread) -> None:
        self._chat.load_thread(thread)

    def _handle_event(self, event) -> None:
        self._turn.handle_event(event)

    def _start_turn(self, prompt: str) -> None:
        self._turn.start_turn(prompt)

    def _approval_pending(self) -> bool:
        return self._chat.approval_pending()

    def _refresh_approval_banner(self) -> None:
        self._chat.refresh_approval_banner()

    def _sync_composer_blocked(self) -> None:
        self._chat.sync_composer_blocked()

    def _composer_blocked(self) -> bool:
        return self._chat.composer_blocked()

    def _restore_composer_draft(self) -> None:
        self._chat.restore_composer_draft()

    def _restore_bindings_for_text(self, text: str) -> None:
        self._chat.restore_bindings_for_text(text)

    def _record_mention_binding(self, candidate: MentionCandidate) -> None:
        self._chat.record_mention_binding(candidate)

    def _sync_mention_popup(self) -> None:
        self._chat.sync_mention_popup()

    def _apply_mention_candidate(self, index: int) -> None:
        self._chat.apply_mention_candidate(index)

    def _transcript_targets(self):
        return self._chat.transcript_targets()

    def _open_approval_overlay(self) -> None:
        self._chat.open_approval_overlay()

    def _open_user_input_overlay(self, **kwargs) -> None:
        self._chat.open_user_input_overlay(**kwargs)

    def _on_overlay_closed(self, result=None) -> None:
        self._chat.on_overlay_closed(result)

    def _append_system(self, text: str) -> None:
        self._turn.append_system(text)

    def _turn_finished(self) -> None:
        self._turn.turn_finished()

    def _handle_slash_command(self, name: str, arg: str) -> None:
        self._turn.handle_slash_command(name, arg)

    def _ensure_thread(self) -> None:
        self._turn.ensure_thread()

    def _populate_home_menu(self) -> None:
        self._home.populate_home_menu()

    def _enter_chat_with_thread(self, thread_id: str) -> None:
        self._home.enter_chat_with_thread(thread_id)

    def _resolve_thread(self, thread_id: str) -> Thread:
        return self._chat.resolve_thread(thread_id)

    def _schedule_transcript_reflow(self) -> None:
        self._chat.schedule_transcript_reflow()

    def _apply_transcript_reflow(self) -> None:
        self._chat.apply_transcript_reflow()

    def _schedule_composer_draft_save(self) -> None:
        self._chat.schedule_composer_draft_save()

    def _flush_composer_draft(self) -> None:
        self._chat.flush_composer_draft()

    def _update_reverse_search(self) -> None:
        self._chat.update_reverse_search()

    def _on_worker_error(self, message: str) -> None:
        self._turn.on_worker_error(message)

    def _enrich_turn_summary_from_thread(self) -> None:
        self._turn.enrich_turn_summary_from_thread()
