from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import OptionList, Static, TextArea

from agent.tui.composer import ComposerTextArea
from agent.tui.composer_chrome import format_composer_meta, format_mode_badge
from agent.tui.footer_state import FooterProps, format_footer, resolve_footer_mode
from agent.tui.input_history import InputHistory
from agent.tui.statusline import load_statusline_settings
from textual.widgets.option_list import Option

from agent.cancel import CancelToken, CancelledError
from agent.config import Config
from agent.profiles import (
    apply_merged_to_resolve_kwargs,
    merge_layered_config,
    thread_cost_summary,
)
from agent.events import AgentEvent
from agent.git import detect_repo_root
from agent.models import Thread, new_id, utc_now_iso
from agent.recording.store import RunStore
from agent.store import ThreadStore
from agent.memories import SuggestQueue
from agent.threads_picker import (
    thread_picker_label,
    thread_picker_label_summary,
    thread_resume_preview,
)
from agent.tui.cells.base import SystemCell, UserMessageCell
from agent.tui.cells.error import render_approval_banner_text
from agent.tui.overlay import TranscriptOverlayScreen
from agent.tui.approval_overlay import ApprovalOverlayScreen
from agent.tui.diff_render import infer_path_from_diff
from agent.tui.user_input_overlay import UserInputOverlayScreen
from agent.tui.composer_draft import (
    clear_composer_draft,
    load_composer_draft,
    save_composer_draft,
    ComposerDraft,
)
from agent.tui.mention_popup import (
    MentionBinding,
    MentionCandidate,
    apply_mention,
    binding_from_candidate,
    list_mention_candidates,
    rebuild_bindings_from_text,
)
from agent.tui.runner import run_turn_in_thread
from agent.tui.transcript_reflow import REFLOW_DEBOUNCE_SEC, TranscriptReflowState
from agent.tui.slash_commands import (
    TuiSlashState,
    execute_slash_command,
    parse_slash_command,
)
from agent.tui.status_row import StatusRow
from agent.tui.theme import AGENT_LOGO, GROK_CSS, app_version, home_menu_options
from agent.tui.messages import AgentEventMessage, WorkerErrorMessage, WorkerFinishedMessage
from agent.tui.transcript_controller import TranscriptController
from agent.tui.transcript_pane import TranscriptPane, compose_transcript_shell
from agent.tui.view_model import (
    TuiState,
    apply_event_to_state,
    approval_key_to_response,
    filter_session_threads,
    thread_transcript_from_store,
)
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
        self._worker: threading.Thread | None = None
        self._index_worker: threading.Thread | None = None
        self._approval_queue: queue.Queue[str] = queue.Queue()
        self._user_input_response_queue: queue.Queue[dict[str, str | None]] = (
            queue.Queue()
        )
        self._event_queue: queue.SimpleQueue[AgentEvent] = queue.SimpleQueue()
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
        self._statusline_settings = load_statusline_settings(self._config.config_path)
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
            cli_model_profile=self._slash.model_profile_override or self._model_profile or None,
            cli_model=self._slash.model_override,
        )
        kwargs = apply_merged_to_resolve_kwargs(merged)
        return Config.resolve(cwd=self._config.cwd, **kwargs)

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Vertical(id="app_shell"):
            with Vertical(id="main"):
                with Vertical(id="home_panel"):
                    with Vertical(id="home_stack"):
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
        self._status_row = StatusRow(self.query_one("#status_row", Static))
        self.query_one("#status_row", Static).display = False
        self.query_one("#approval_banner", Static).display = False
        self.query_one("#resume_preview", Static).display = False
        self.query_one("#mention_popup", OptionList).display = False

        self._set_mode("home")
        self._refresh_chrome()
        self.query_one("#input", TextArea).focus()
        self._start_background_thread_index()

    def _start_background_thread_index(self) -> None:
        def worker() -> None:
            try:
                threads = filter_session_threads(
                    self._store.list_thread_meta(), self._config.cwd
                )
                self.call_from_thread(self._on_threads_index_loaded, threads)
            except Exception as exc:
                self.call_from_thread(self._on_threads_index_failed, str(exc))

        self._index_worker = threading.Thread(target=worker, daemon=True)
        self._index_worker.start()

    def _on_threads_index_loaded(self, threads: list[Thread]) -> None:
        self._session_threads = threads
        self._threads_loading = False
        if self._mode == "home":
            self._populate_home_menu()
        self._refresh_chrome()
        self._maybe_enter_pending_chat()

    def _on_threads_index_failed(self, message: str) -> None:
        self._threads_loading = False
        self.query_one("#composer_footer", Static).update(
            f"[yellow]Could not list sessions:[/yellow] {message}"
        )

    def _maybe_enter_pending_chat(self) -> None:
        if self._pending_chat == "none":
            return
        if self._pending_chat == "resume_last":
            if not self._session_threads:
                self._pending_chat = "none"
                return
            self._enter_chat_with_thread(self._session_threads[0].id)
            return
        if self._pending_chat == "thread_id" and self._requested_thread_id:
            self._enter_chat_with_thread(self._requested_thread_id)

    def _enter_chat_with_thread(self, thread_id: str) -> None:
        self._pending_chat = "none"
        self.query_one("#composer_footer", Static).update("[dim]Loading session…[/dim]")

        def worker() -> None:
            try:
                thread = self._resolve_thread(thread_id)
                self.call_from_thread(self._on_chat_thread_loaded, thread)
            except Exception as exc:
                self.call_from_thread(self._on_chat_thread_failed, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_chat_thread_loaded(self, thread: Thread) -> None:
        self._load_thread(thread)
        self._set_mode("chat")
        self._restore_composer_draft()
        self.query_one("#input", ComposerTextArea).focus()

    def _on_chat_thread_failed(self, message: str) -> None:
        self._pending_chat = "none"
        self._set_mode("home")
        self.query_one("#composer_footer", Static).update(
            f"[yellow]Could not load session:[/yellow] {message}"
        )

    def on_resize(self) -> None:
        width = max(40, self.size.width)
        if self._reflow.observe_width(width):
            if self._turn_running:
                self._reflow.resize_during_stream = True
            self._schedule_transcript_reflow()
        self._refresh_chrome()

    def _schedule_transcript_reflow(self) -> None:
        if self._reflow_timer is not None:
            self._reflow_timer.stop()
        self._reflow_timer = self.set_timer(
            REFLOW_DEBOUNCE_SEC,
            self._apply_transcript_reflow,
            name="transcript_reflow",
        )

    def _apply_transcript_reflow(self) -> None:
        self._reflow_timer = None
        if self._mode != "chat":
            return
        width = max(40, self.size.width)
        if not self._reflow.needs_rebuild(width):
            return
        scroll, cells = self._transcript_targets()
        pane = TranscriptPane(cells, scroll)
        pin_tail = pane.is_near_bottom() or self._transcript.follow_tail
        self._sync_transcript(rebuild=True)
        if pin_tail:
            pane.scroll_to_end(force=True, follow=self._transcript.follow_tail)
        self._reflow.mark_rebuilt(width)

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
        if include_context is None:
            include_context = True
        if self._slash_has_overrides():
            self._config = self._resolve_config()

        self.query_one("#header", Static).update(str(self._config.cwd))

        self.query_one("#mode_badge", Static).update(
            format_mode_badge(plan_mode=self._slash.plan_mode)
        )
        context_snapshot = None
        if include_context and self._thread is not None:
            from agent.context_meter import build_context_snapshot

            context_snapshot = build_context_snapshot(
                self._config, self._thread, ctx_settings=self._config.context
            )
        self.query_one("#composer_meta", Static).update(
            format_composer_meta(
                config=self._config,
                model_short=self._active_model_short(),
                profile=self._active_model_profile_name(),
                plan_mode=self._slash.plan_mode,
                thread=self._thread,
                include_context=include_context,
                turn_running=self._turn_running,
                routing_note=self._state.routing_note if include_context else "",
                session_auto_approve=self._session_auto_approve,
                pending_approval=bool(self._state.pending_approval_summary),
                memories_pending=self._memories_pending_count(),
                statusline_settings=self._statusline_settings,
                context_snapshot=context_snapshot,
            )
        )
        footer_mode = resolve_footer_mode(
            pending_approval=bool(self._state.pending_approval_summary),
            pending_user_input=bool(self._state.pending_user_input_question),
            turn_running=self._turn_running,
            threads_loading=self._threads_loading,
            screen_mode=self._mode,
            reverse_search_active=self._reverse_search_active,
            resume_highlight=self._mode == "resume" and bool(self._resume_preview_text),
        )
        self.query_one("#composer_footer", Static).update(
            format_footer(
                FooterProps(
                    mode=footer_mode,
                    app_version=self._app_version,
                    screen_mode=self._mode,
                    turn_running=self._turn_running,
                    threads_loading=self._threads_loading,
                    reverse_search_query=self._reverse_search_query,
                    reverse_search_match=self._reverse_search_match,
                    resume_preview=self._resume_preview_text,
                    terminal_width=self.size.width,
                )
            )
        )
        preview = self.query_one("#resume_preview", Static)
        if self._mode == "resume" and self._resume_preview_text:
            preview.update(self._resume_preview_text)
            preview.display = True
        else:
            preview.display = False
        self._refresh_approval_banner()

    def _approval_pending(self) -> bool:
        return bool(self._state.pending_approval_summary)

    def _refresh_approval_banner(self) -> None:
        banner = self.query_one("#approval_banner", Static)
        inp = self.query_one("#input", ComposerTextArea)
        bottom = self.query_one("#bottom_chrome")
        if self._approval_pending():
            if self._approval_overlay_open:
                banner.update(
                    "[dim]Approval dialog open — y/n/a/A or Esc[/dim]"
                )
                banner.display = True
            else:
                source_path = infer_path_from_diff(self._state.pending_approval_diff)
                banner.update(
                    render_approval_banner_text(
                        self._state.pending_approval_summary or "",
                        diff_preview=self._state.pending_approval_diff,
                        tool_name=self._state.pending_approval_tool,
                        source_path=source_path,
                    )
                )
                banner.display = True
            banner.add_class("approval-active")
            bottom.add_class("approval-active")
            inp.placeholder = "y / n / a / A"
        else:
            banner.display = False
            banner.remove_class("approval-active")
            bottom.remove_class("approval-active")
            inp.placeholder = "Message the agent…"
        self._sync_composer_blocked()

    def _open_approval_overlay(self) -> None:
        if self._approval_overlay_open or not self._approval_pending():
            return
        if isinstance(self.screen, ApprovalOverlayScreen):
            return

        summary = self._state.pending_approval_summary or ""
        diff_preview = self._state.pending_approval_diff
        tool_name = self._state.pending_approval_tool
        source_path = infer_path_from_diff(diff_preview)

        def on_done(key: str | None) -> None:
            self._approval_overlay_open = False
            if key:
                self._approval_queue.put(key)
                if key in ("A", "a"):
                    self._session_auto_approve = True
                self._state.pending_approval_summary = None
                self._state.pending_approval_diff = None
                self._state.pending_approval_tool = None
            self._sync_composer_blocked()
            self.query_one("#input", ComposerTextArea).focus()
            self._refresh_approval_banner()
            self._refresh_chrome()

        self._approval_overlay_open = True
        self.push_screen(
            ApprovalOverlayScreen(
                summary=summary,
                diff_preview=diff_preview,
                tool_name=tool_name,
                source_path=source_path,
            ),
            on_done,
        )
        self._sync_composer_blocked()

    def _populate_home_menu(self) -> None:
        menu = self.query_one("#home_menu", OptionList)
        menu.clear_options()
        for option_id, label in home_menu_options(has_sessions=bool(self._session_threads)):
            shortcut = {
                "new": "Enter",
                "resume": "Ctrl+S",
                "quit": "Ctrl+Q",
            }.get(option_id, "")
            padding = max(1, 40 - len(label))
            menu.add_option(Option(f"{label}{' ' * padding}{shortcut}", id=option_id))

    def _set_mode(self, mode: Literal["home", "chat", "resume"]) -> None:
        self._mode = mode
        home = self.query_one("#home_panel")
        transcript = self.query_one("#transcript", VerticalScroll)
        home_menu = self.query_one("#home_menu", OptionList)
        resume_menu = self.query_one("#resume_menu", OptionList)

        if mode == "home":
            home.display = True
            transcript.display = False
            home_menu.display = True
            resume_menu.display = False
            self._populate_home_menu()
            self._refresh_chrome()
            return

        if mode == "resume":
            home.display = True
            transcript.display = False
            home_menu.display = False
            resume_menu.display = True
            resume_menu.clear_options()
            for thread in self._session_threads:
                resume_menu.add_option(
                    Option(thread_picker_label_summary(thread), id=thread.id)
                )
            resume_menu.add_option(Option("← Back", id="__back__"))
            if self._session_threads:
                self._resume_preview_text = (
                    f"[dim]Preview[/dim]  {thread_resume_preview(self._session_threads[0])}"
                )
            self._refresh_chrome()
            return

        home.display = False
        transcript.display = True
        home_menu.display = False
        resume_menu.display = False
        self._transcript.follow_tail = True
        self._refresh_chrome()
        self._sync_transcript(rebuild=True)

    def _resolve_thread(self, thread_id: str) -> Thread:
        try:
            return self._store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [
                t
                for t in self._store.list_thread_meta()
                if t.id.startswith(thread_id)
            ]
            if len(matches) == 1:
                return self._store.load_thread(matches[0].id)
            raise

    def _load_thread(self, thread: Thread) -> None:
        if not thread.turns:
            thread = self._store.load_thread(thread.id)
        self._thread = thread
        self._state = TuiState()
        self._state.transcript = thread_transcript_from_store(thread)
        self._transcript.reset()
        summary = thread_cost_summary(thread)
        if summary.get("estimated_cost_usd"):
            self._last_turn_cost = float(summary["estimated_cost_usd"])

    def _composer_blocked(self) -> bool:
        return isinstance(
            self.screen,
            (ApprovalOverlayScreen, UserInputOverlayScreen, TranscriptOverlayScreen),
        )

    def _sync_composer_blocked(self) -> None:
        inp = self.query_one("#input", ComposerTextArea)
        if self._composer_blocked():
            inp.read_only = True
            return
        inp.read_only = False

    def _restore_bindings_for_text(self, text: str) -> None:
        self._composer_bindings = rebuild_bindings_from_text(text, self._config.cwd)

    def _restore_composer_draft(self) -> None:
        if not self._thread:
            return
        draft = load_composer_draft(self._config.cwd, self._thread.id)
        inp = self.query_one("#input", ComposerTextArea)
        inp.text = draft.text
        self._composer_bindings = list(draft.bindings)
        inp.reset_history_navigation()

    def _schedule_composer_draft_save(self) -> None:
        if self._composer_draft_timer is not None:
            self._composer_draft_timer.stop()
        self._composer_draft_timer = self.set_timer(
            0.4,
            self._flush_composer_draft,
            name="composer_draft",
        )

    def _flush_composer_draft(self) -> None:
        self._composer_draft_timer = None
        if not self._thread:
            return
        inp = self.query_one("#input", ComposerTextArea)
        bindings = list(self._composer_bindings)
        if inp.text.strip() and not bindings:
            bindings = rebuild_bindings_from_text(inp.text, self._config.cwd)
        save_composer_draft(
            self._config.cwd,
            self._thread.id,
            ComposerDraft(text=inp.text, bindings=bindings),
        )

    def _record_mention_binding(self, candidate: MentionCandidate) -> None:
        binding = binding_from_candidate(candidate)
        self._composer_bindings = [
            b for b in self._composer_bindings if b.mention != binding.mention
        ]
        self._composer_bindings.append(binding)
        self._schedule_composer_draft_save()

    def _transcript_targets(self) -> tuple[VerticalScroll, Vertical]:
        scroll = self.query_one("#transcript", VerticalScroll)
        cells = scroll.query_one("#transcript_cells", Vertical)
        return scroll, cells

    def _sync_transcript(self, *, rebuild: bool = False) -> None:
        scroll, cells = self._transcript_targets()
        if rebuild:
            self._transcript.rebuild_all(cells, self._state, scroll=scroll)
            TranscriptPane(cells, scroll).scroll_to_end(
                force=True, follow=True
            )
        else:
            self._transcript.sync(
                cells,
                self._state,
                scroll=scroll,
                force_scroll=self._turn_running,
            )

    def _event_on_transcript(self, widget) -> bool:
        scroll = self.query_one("#transcript", VerticalScroll)
        node = widget
        while node is not None:
            if node is scroll:
                return True
            node = getattr(node, "parent", None)
        return False

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if self._mode != "chat":
            return
        if self._event_on_transcript(event.widget):
            self._transcript.follow_tail = False

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        if self._mode != "chat":
            return
        if self._event_on_transcript(event.widget):
            self.call_after_refresh(self._maybe_resume_transcript_follow)

    def _maybe_resume_transcript_follow(self) -> None:
        if self._mode != "chat":
            return
        scroll, cells = self._transcript_targets()
        if TranscriptPane(cells, scroll).is_near_bottom():
            self._transcript.follow_tail = True

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "resume_menu":
            return
        option_id = str(event.option.id or "")
        if option_id in ("", "__back__"):
            self._resume_preview_text = ""
        else:
            try:
                thread = self._resolve_thread(option_id)
                self._resume_preview_text = (
                    f"[dim]Preview[/dim]  {thread_resume_preview(thread)}"
                )
            except Exception:
                self._resume_preview_text = "[dim]Preview unavailable[/dim]"
        self._refresh_chrome()

    def _sync_mention_popup(self) -> None:
        popup = self.query_one("#mention_popup", OptionList)
        inp = self.query_one("#input", ComposerTextArea)
        if self._approval_pending() or self._mode not in ("chat", "home"):
            popup.display = False
            popup.clear_options()
            self._mention_candidates = []
            return
        self._mention_candidates = list_mention_candidates(inp.text, self._config.cwd)
        if not self._mention_candidates:
            popup.display = False
            popup.clear_options()
            self._mention_highlight = 0
            return
        if self._mention_highlight >= len(self._mention_candidates):
            self._mention_highlight = 0
        popup.clear_options()
        for idx, cand in enumerate(self._mention_candidates):
            tag = "skill" if cand.kind == "skill" else "file"
            popup.add_option(Option(f"[dim]{tag}[/dim] {cand.label}", id=str(idx)))
        popup.display = True
        self._refresh_mention_highlight()

    def _refresh_mention_highlight(self) -> None:
        popup = self.query_one("#mention_popup", OptionList)
        if not self._mention_candidates:
            return
        idx = max(0, min(self._mention_highlight, len(self._mention_candidates) - 1))
        self._mention_highlight = idx
        popup.highlighted = idx

    def _apply_mention_candidate(self, index: int) -> None:
        if index < 0 or index >= len(self._mention_candidates):
            return
        cand = self._mention_candidates[index]
        inp = self.query_one("#input", ComposerTextArea)
        inp.text = apply_mention(inp.text, cand)
        self._record_mention_binding(cand)
        self._mention_candidates = []
        self.query_one("#mention_popup", OptionList).display = False
        inp.focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "input":
            return
        self._sync_mention_popup()
        if self._thread:
            self._schedule_composer_draft_save()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = str(event.option_id or "")
        widget_id = event.option_list.id

        if widget_id == "mention_popup":
            try:
                self._apply_mention_candidate(int(option_id))
            except ValueError:
                pass
            return

        if widget_id == "home_menu":
            if option_id == "new":
                self.query_one("#input", TextArea).focus()
            elif option_id == "resume":
                self.action_resume_session()
            elif option_id == "quit":
                self.action_quit_app()
            return

        if widget_id == "resume_menu":
            if option_id == "__back__":
                self._set_mode("home")
                return
            thread = self._resolve_thread(option_id)
            self._load_thread(thread)
            self._set_mode("chat")
            self._restore_composer_draft()
            self.query_one("#input", TextArea).focus()

    def on_text_area_submitted(self, event: TextArea.Submitted) -> None:
        self._submit_input(event.text_area.text)

    def _expand_file_mention(self, text: str) -> str | None:
        """Complete the active @file or @skill mention on Tab."""
        if not self._mention_candidates:
            self._mention_candidates = list_mention_candidates(text, self._config.cwd)
        if not self._mention_candidates:
            return None
        idx = max(0, min(self._mention_highlight, len(self._mention_candidates) - 1))
        return apply_mention(text, self._mention_candidates[idx])

    def _open_user_input_overlay(
        self,
        *,
        question: str,
        options: list[str],
        allow_free_text: bool,
        question_index: int = 1,
        question_total: int = 1,
    ) -> None:
        if isinstance(self.screen, UserInputOverlayScreen):
            return

        def on_done(result: dict[str, str | None] | None) -> None:
            if result is None:
                self._user_input_response_queue.put({"cancelled": "1"})
            else:
                self._user_input_response_queue.put(result)
            self._sync_composer_blocked()
            self.query_one("#input", ComposerTextArea).focus()
            self._refresh_chrome()

        self.push_screen(
            UserInputOverlayScreen(
                question=question,
                options=options or None,
                allow_free_text=allow_free_text,
                question_index=question_index,
                question_total=question_total,
            ),
            on_done,
        )
        self._sync_composer_blocked()

    def _submit_input(self, raw_text: str) -> None:
        text = raw_text.strip()
        if not text:
            return
        inp = self.query_one("#input", ComposerTextArea)
        inp.clear()
        inp.reset_history_navigation()

        if self._reverse_search_active:
            return

        if self._approval_pending():
            response = approval_key_to_response(text)
            if response is None:
                self._append_system(f"Invalid key {text!r} — use y, n, a, or A")
                return
            self._approval_queue.put(response)
            if response in ("A", "a"):
                self._session_auto_approve = True
            self._state.pending_approval_summary = None
            self._state.pending_approval_diff = None
            self._state.pending_approval_tool = None
            self._refresh_approval_banner()
            self._refresh_chrome()
            return

        parsed = parse_slash_command(text)
        if parsed is not None:
            self._handle_slash_command(*parsed)
            return

        if self._turn_running:
            self._append_system("Turn already running — Ctrl+C to cancel.")
            return

        if self._mode == "home":
            self._set_mode("chat")

        if not self._thread:
            self._ensure_thread()

        if self._thread:
            clear_composer_draft(self._config.cwd, self._thread.id)
        self._composer_bindings = []

        self._input_history.add(text)
        self._state.transcript.append(UserMessageCell(text=text))
        self._sync_transcript()
        self._start_turn(text)

    def _ensure_thread(self) -> None:
        if self._thread:
            return
        repo_root = detect_repo_root(self._config.cwd)
        config = self._resolve_config()
        self._thread = Thread(
            id=new_id(),
            cwd=str(self._config.cwd),
            model=config.model,
            repo_root=repo_root,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        self._store.create_thread(self._thread)
        self._session_threads = filter_session_threads(
            self._store.list_thread_meta(), self._config.cwd
        )

    def _handle_slash_command(self, name: str, arg: str) -> None:
        if name == "/expand":
            if self._transcript.toggle_last_expandable(self._state):
                self._sync_transcript()
            return

        if self._turn_running and name not in (
            "/help",
            "/model",
            "/plan",
            "/profile",
            "/why-model",
            "/why",
            "/quit",
            "/exit",
            "/q",
            "/expand",
        ):
            self._append_system("Wait for the current turn to finish.")
            return

        result = execute_slash_command(
            name,
            arg,
            state=self._slash,
            config=self._resolve_config(),
            store=self._store,
            thread=self._thread,
        )
        if not result.handled:
            return

        if result.quit_app:
            self.action_quit_app()
            return

        if result.clear_session:
            self._thread = None
            self._state = TuiState()
            self._transcript.reset()
            self._last_turn_cost = None
            if result.enter_chat:
                self._set_mode("chat")
            else:
                self._set_mode("home")

        if result.reload_thread and self._thread:
            try:
                self._thread = self._store.load_thread(self._thread.id)
                self._state.transcript = thread_transcript_from_store(self._thread)
                self._transcript.reset()
            except FileNotFoundError:
                pass
            if self._mode == "chat":
                self._sync_transcript(rebuild=True)

        if result.message:
            if self._mode == "home" and not result.enter_chat:
                self.query_one("#composer_footer", Static).update(result.message)
            else:
                if self._mode == "home":
                    self._set_mode("chat")
                self._append_system(result.message)

        self._refresh_chrome()

    def _start_turn(self, prompt: str) -> None:
        assert self._thread is not None
        config = self._resolve_config()
        self._thread.model = config.model
        self._state.turn_model = config.model.split("/")[-1]
        self._state.last_user_prompt = prompt
        self._state.turn_fallback_used = False
        self._state.turn_cost = None
        from agent.model_routing import resolve_model_profile_from_task

        profile = resolve_model_profile_from_task(
            prompt,
            cli_model_profile=self._slash.model_profile_override,
            cwd=config.cwd,
        )
        self._state.routing_note = f"profile={profile}" if profile else ""
        self._slash.last_routing_prompt = prompt
        self._cancel = CancelToken()
        self._turn_running = True
        if self._status_row:
            if not self._status_row.interval_active:
                self._status_row.bind_interval(self)
            self._status_row.start_turn()
        self._refresh_chrome()

        def on_event(agent_event: AgentEvent) -> None:
            # Do not use call_from_thread here — it blocks the worker on future.result()
            # while the UI runs heavy transcript/context updates.
            self._event_queue.put(agent_event)
            self.post_message(AgentEventMessage())

        def worker() -> None:
            worker_error: str | None = None
            try:
                run_turn_in_thread(
                    self._thread,  # type: ignore[arg-type]
                    prompt,
                    config,
                    self._store,
                    on_event=on_event,
                    cancel_token=self._cancel,
                    approval_queue=self._approval_queue,
                    user_input_response_queue=self._user_input_response_queue,
                    session_auto_approve=self._session_auto_approve,
                    plan_mode=self._slash.plan_mode,
                )
            except CancelledError:
                pass
            except Exception as exc:
                worker_error = str(exc)
            if worker_error:
                self.post_message(WorkerErrorMessage(worker_error))
            self.post_message(WorkerFinishedMessage())

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def on_agent_event_message(self, _message: AgentEventMessage) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

    def on_worker_finished_message(self, _message: WorkerFinishedMessage) -> None:
        self._turn_finished()

    def on_worker_error_message(self, message: WorkerErrorMessage) -> None:
        self._on_worker_error(message.error)

    def _handle_event(self, event: AgentEvent) -> None:
        if event.type == "tool.pending" and self._mode == "chat":
            scroll, cells = self._transcript_targets()
            self._transcript.finalize_assistant_stream(
                cells, self._state, scroll=scroll
            )

        self._state = apply_event_to_state(self._state, event)

        if event.type == "turn.completed" and self._thread and self._thread.turns:
            last = self._thread.turns[-1]
            if last.usage.estimated_cost_usd:
                self._last_turn_cost = last.usage.estimated_cost_usd
                self._state.turn_cost = last.usage.estimated_cost_usd
            self._state.turn_fallback_used = bool(last.usage.fallback_used)
            if last.usage.model_used:
                self._state.turn_model = last.usage.model_used.split("/")[-1]

        if event.type == "turn.completed" and self._mode == "chat":
            self._transcript.commit_orphan_live_stream(self._state)
            self._transcript.clear_live_stream_state()

        if event.type == "user_input.requested":
            self._open_user_input_overlay(
                question=str(event.data.get("question", "")),
                options=list(event.data.get("options") or []),
                allow_free_text=bool(event.data.get("allow_free_text", True)),
                question_index=int(event.data.get("question_index", 1) or 1),
                question_total=int(event.data.get("question_total", 1) or 1),
            )

        if event.type == "approval.requested":
            self._open_approval_overlay()

        self._sync_transcript()
        include_context = event.type not in ("agent.delta", "agent.reasoning")
        self._refresh_chrome(include_context=include_context)

    def _watch_turn_worker(self) -> None:
        """Recover UI state if the worker exited without posting WorkerFinishedMessage."""
        if not self._turn_running:
            return
        if self._worker is not None and self._worker.is_alive():
            return
        self._append_system(
            "Turn worker stopped unexpectedly (UI was still waiting). "
            "Check ~/.agent-cli/logs/ for the last action."
        )
        self._turn_finished()

    def _turn_finished(self) -> None:
        self._turn_running = False
        if self._reflow.resize_during_stream:
            self._schedule_transcript_reflow()
        if self._status_row:
            self._status_row.stop_turn(self._state.status_line)
        if self._thread:
            try:
                self._thread = self._store.load_thread(self._thread.id)
            except FileNotFoundError:
                pass
            self._enrich_turn_summary_from_thread()
        self._session_threads = filter_session_threads(
            self._store.list_thread_meta(), self._config.cwd
        )
        self._refresh_chrome()

    def _enrich_turn_summary_from_thread(self) -> None:
        """Fill live TurnSummaryCell stats after thread reload (events lack stats payload)."""
        from agent.turn_stats import aggregate_turn_stats
        from agent.tui.cells.base import TurnSummaryCell

        if not self._thread or not self._thread.turns:
            return
        stats = aggregate_turn_stats(self._thread.turns[-1])
        for cell in reversed(self._state.transcript):
            if isinstance(cell, TurnSummaryCell):
                cell.files_changed = stats.files_touched
                cell.lines_added = stats.lines_added
                cell.lines_removed = stats.lines_removed
                cell.commands_run = stats.commands_run
                last_usage = self._thread.turns[-1].usage
                if last_usage.model_used:
                    cell.model = last_usage.model_used.split("/")[-1]
                cell.fallback_used = bool(last_usage.fallback_used)
                if self._state.routing_note:
                    cell.routing_note = self._state.routing_note
                break
        if self._mode == "chat":
            self._sync_transcript()

    def _append_system(self, text: str) -> None:
        self._state.transcript.append(SystemCell(text=text))
        if self._mode == "chat":
            self._sync_transcript()
        else:
            self.query_one("#composer_footer", Static).update(f"[yellow]{text}[/yellow]")

    def _on_worker_error(self, message: str) -> None:
        from agent.tui.cells.base import ErrorCell

        if self._mode == "home":
            self._set_mode("chat")
        self._state.transcript.append(ErrorCell(message=message, severity="error"))
        self._sync_transcript(rebuild=True)

    def action_new_session(self) -> None:
        if self._turn_running:
            self._append_system("Wait for the current turn to finish.")
            return
        self._thread = None
        self._state = TuiState()
        self._transcript.reset()
        self._last_turn_cost = None
        self._slash = TuiSlashState()
        self._session_auto_approve = False
        self._composer_bindings = []
        inp = self.query_one("#input", ComposerTextArea)
        inp.clear()
        self._set_mode("home")
        inp.focus()

    def action_resume_session(self) -> None:
        if self._turn_running:
            self._append_system("Wait for the current turn to finish.")
            return
        if self._threads_loading:
            self._set_mode("home")
            self.query_one("#composer_footer", Static).update(
                "[dim]Still loading sessions…[/dim]"
            )
            return
        if not self._session_threads:
            self._session_threads = filter_session_threads(
                self._store.list_thread_meta(), self._config.cwd
            )
        if not self._session_threads:
            self._set_mode("home")
            self.query_one("#composer_footer", Static).update(
                "No saved sessions in this folder yet — start typing below."
            )
            return
        self._set_mode("resume")

    def action_back_to_home(self) -> None:
        if self._mode == "resume":
            self._set_mode("home")

    def action_cancel_turn(self) -> None:
        if self._turn_running:
            self._cancel.cancel()
            self._append_system("Cancelling…")

    def action_transcript_overlay(self) -> None:
        if self._mode == "chat":
            self.push_screen(
                TranscriptOverlayScreen(self._state),
                callback=self._on_overlay_closed,
            )
            self._sync_composer_blocked()

    def _on_overlay_closed(self, _result: object | None = None) -> None:
        self._sync_composer_blocked()
        if self._mode == "chat":
            self._sync_transcript()
            self.query_one("#input", ComposerTextArea).focus()

    def action_toggle_expand(self) -> None:
        if self._transcript.toggle_last_expandable(self._state):
            self._sync_transcript()

    def on_click(self, event) -> None:
        if self._mode != "chat":
            return
        widget_id = str(event.widget.id or "")
        if not widget_id.startswith("cell-"):
            return
        if self._transcript.toggle_expand(self._state, widget_id):
            self._sync_transcript()

    def action_why_model(self) -> None:
        from agent.model_routing import explain_model_routing

        task = self._slash.last_routing_prompt or self._state.last_user_prompt
        if not task:
            self._append_system("No prompt to analyze yet.")
            return
        self._append_system(
            explain_model_routing(
                task,
                cli_model_profile=self._slash.model_profile_override,
                cwd=self._config.cwd,
            )
        )

    def action_quit_app(self) -> None:
        if self._turn_running:
            self._append_system("Turn running — cancel with Ctrl+C first.")
            return
        self.exit()

    def action_reverse_search(self) -> None:
        if self._reverse_search_active:
            return
        inp = self.query_one("#input", ComposerTextArea)
        self._reverse_search_saved_draft = inp.text
        self._reverse_search_active = True
        self._reverse_search_query = ""
        self._reverse_search_match = ""
        inp.read_only = True
        self._update_reverse_search()

    def action_reverse_search_cancel(self) -> None:
        if not self._reverse_search_active:
            return
        inp = self.query_one("#input", ComposerTextArea)
        self._reverse_search_active = False
        self._reverse_search_query = ""
        self._reverse_search_match = ""
        inp.read_only = False
        inp.text = self._reverse_search_saved_draft
        self._reverse_search_saved_draft = ""
        self._refresh_chrome()

    def action_reverse_search_accept(self) -> None:
        if not self._reverse_search_active:
            return
        match = self._reverse_search_match
        self.action_reverse_search_cancel()
        if match:
            self.query_one("#input", ComposerTextArea).text = match

    def _update_reverse_search(self) -> None:
        self._reverse_search_match = (
            self._input_history.reverse_search(self._reverse_search_query) or ""
        )
        self._refresh_chrome()

    def on_unmount(self) -> None:
        set_approval_input(None)
