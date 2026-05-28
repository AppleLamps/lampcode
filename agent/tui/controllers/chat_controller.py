"""Chat transcript, composer, chrome, and overlay orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical, VerticalScroll
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from agent.models import Thread
from agent.profiles import thread_cost_summary
from agent.tui.approval_overlay import ApprovalOverlayScreen
from agent.tui.cells.error import render_approval_banner_text
from agent.tui.composer import ComposerTextArea
from agent.tui.composer_chrome import format_composer_meta, format_mode_badge
from agent.tui.composer_draft import (
    ComposerDraft,
    clear_composer_draft,
    load_composer_draft,
    save_composer_draft,
)
from agent.tui.diff_render import infer_path_from_diff
from agent.tui.footer_state import FooterProps, format_footer, resolve_footer_mode
from agent.tui.mention_popup import (
    MentionCandidate,
    apply_mention,
    binding_from_candidate,
    list_mention_candidates,
    rebuild_bindings_from_text,
)
from agent.tui.overlay import TranscriptOverlayScreen
from agent.tui.transcript_pane import TranscriptPane
from agent.tui.transcript_reflow import REFLOW_DEBOUNCE_SEC
from agent.tui.user_input_overlay import UserInputOverlayScreen
from agent.tui.view_model import thread_transcript_from_store

if TYPE_CHECKING:
    from agent.tui.app import AgentTuiApp


class ChatController:
    def __init__(self, app: AgentTuiApp) -> None:
        self._app = app

    def apply_chat_layout(self) -> None:
        home = self._app.query_one("#home_panel")
        transcript = self._app.query_one("#transcript")
        home_menu = self._app.query_one("#home_menu", OptionList)
        resume_menu = self._app.query_one("#resume_menu", OptionList)
        home.display = False
        transcript.display = True
        home_menu.display = False
        resume_menu.display = False
        self._app._transcript.follow_tail = True
        self._app._refresh_chrome()
        self.sync_transcript(rebuild=True)

    def resolve_thread(self, thread_id: str) -> Thread:
        try:
            return self._app._store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [
                t
                for t in self._app._store.list_thread_meta()
                if t.id.startswith(thread_id)
            ]
            if len(matches) == 1:
                return self._app._store.load_thread(matches[0].id)
            raise

    def load_thread(self, thread: Thread) -> None:
        if not thread.turns:
            thread = self._app._store.load_thread(thread.id)
        self._app._thread = thread
        from agent.tui.view_model import TuiState

        self._app._state = TuiState()
        self._app._state.transcript = thread_transcript_from_store(thread)
        self._app._transcript.reset()
        summary = thread_cost_summary(thread)
        if summary.get("estimated_cost_usd"):
            self._app._last_turn_cost = float(summary["estimated_cost_usd"])

    def restore_bindings_for_text(self, text: str) -> None:
        self._app._composer_bindings = rebuild_bindings_from_text(
            text, self._app._config.cwd
        )

    def restore_composer_draft(self) -> None:
        if not self._app._thread:
            return
        draft = load_composer_draft(self._app._config.cwd, self._app._thread.id)
        inp = self._app.query_one("#input", ComposerTextArea)
        inp.text = draft.text
        self._app._composer_bindings = list(draft.bindings)
        inp.reset_history_navigation()

    def schedule_composer_draft_save(self) -> None:
        if self._app._composer_draft_timer is not None:
            self._app._composer_draft_timer.stop()
        self._app._composer_draft_timer = self._app.set_timer(
            0.4,
            self.flush_composer_draft,
            name="composer_draft",
        )

    def flush_composer_draft(self) -> None:
        self._app._composer_draft_timer = None
        if not self._app._thread:
            return
        inp = self._app.query_one("#input", ComposerTextArea)
        bindings = list(self._app._composer_bindings)
        if inp.text.strip() and not bindings:
            bindings = rebuild_bindings_from_text(inp.text, self._app._config.cwd)
        save_composer_draft(
            self._app._config.cwd,
            self._app._thread.id,
            ComposerDraft(text=inp.text, bindings=bindings),
        )

    def record_mention_binding(self, candidate: MentionCandidate) -> None:
        binding = binding_from_candidate(candidate)
        self._app._composer_bindings = [
            b for b in self._app._composer_bindings if b.mention != binding.mention
        ]
        self._app._composer_bindings.append(binding)
        self.schedule_composer_draft_save()

    def transcript_targets(self) -> tuple[VerticalScroll, Vertical]:
        scroll = self._app.query_one("#transcript", VerticalScroll)
        cells = scroll.query_one("#transcript_cells", Vertical)
        return scroll, cells

    def sync_transcript(
        self,
        *,
        rebuild: bool = False,
        stream_only: bool = False,
    ) -> None:
        scroll, cells = self.transcript_targets()
        if rebuild:
            self._app._transcript.rebuild_all(cells, self._app._state, scroll=scroll)
            TranscriptPane(cells, scroll).scroll_to_end(force=True, follow=True)
        elif stream_only:
            self._app._transcript.sync_stream_only(
                cells,
                self._app._state,
                scroll=scroll,
                force_scroll=self._app._turn_running,
            )
        else:
            self._app._transcript.sync(
                cells,
                self._app._state,
                scroll=scroll,
                force_scroll=self._app._turn_running,
            )

    def schedule_transcript_reflow(self) -> None:
        if self._app._reflow_timer is not None:
            self._app._reflow_timer.stop()
        self._app._reflow_timer = self._app.set_timer(
            REFLOW_DEBOUNCE_SEC,
            self.apply_transcript_reflow,
            name="transcript_reflow",
        )

    def apply_transcript_reflow(self) -> None:
        self._app._reflow_timer = None
        if self._app._mode != "chat":
            return
        width = max(40, self._app.size.width)
        if not self._app._reflow.needs_rebuild(width):
            return
        scroll, cells = self.transcript_targets()
        pane = TranscriptPane(cells, scroll)
        pin_tail = pane.is_near_bottom() or self._app._transcript.follow_tail
        self.sync_transcript(rebuild=True)
        if pin_tail:
            pane.scroll_to_end(force=True, follow=self._app._transcript.follow_tail)
        self._app._reflow.mark_rebuilt(width)

    def on_resize(self) -> None:
        width = max(40, self._app.size.width)
        if self._app._reflow.observe_width(width):
            if self._app._turn_running:
                self._app._reflow.resize_during_stream = True
            self.schedule_transcript_reflow()
        self._app._refresh_chrome()

    def event_on_transcript(self, widget) -> bool:
        scroll = self._app.query_one("#transcript", VerticalScroll)
        node = widget
        while node is not None:
            if node is scroll:
                return True
            node = getattr(node, "parent", None)
        return False

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if self._app._mode != "chat":
            return
        if self.event_on_transcript(event.widget):
            self._app._transcript.follow_tail = False

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        if self._app._mode != "chat":
            return
        if self.event_on_transcript(event.widget):
            self._app.call_after_refresh(self.maybe_resume_transcript_follow)

    def maybe_resume_transcript_follow(self) -> None:
        if self._app._mode != "chat":
            return
        scroll, cells = self.transcript_targets()
        if TranscriptPane(cells, scroll).is_near_bottom():
            self._app._transcript.follow_tail = True

    def sync_mention_popup(self) -> None:
        popup = self._app.query_one("#mention_popup", OptionList)
        inp = self._app.query_one("#input", ComposerTextArea)
        if self.approval_pending() or self._app._mode not in ("chat", "home"):
            popup.display = False
            popup.clear_options()
            self._app._mention_candidates = []
            return
        self._app._mention_candidates = list_mention_candidates(
            inp.text, self._app._config.cwd
        )
        if not self._app._mention_candidates:
            popup.display = False
            popup.clear_options()
            self._app._mention_highlight = 0
            return
        if self._app._mention_highlight >= len(self._app._mention_candidates):
            self._app._mention_highlight = 0
        popup.clear_options()
        for idx, cand in enumerate(self._app._mention_candidates):
            tag = "skill" if cand.kind == "skill" else "file"
            popup.add_option(Option(f"[dim]{tag}[/dim] {cand.label}", id=str(idx)))
        popup.display = True
        self.refresh_mention_highlight()

    def refresh_mention_highlight(self) -> None:
        popup = self._app.query_one("#mention_popup", OptionList)
        if not self._app._mention_candidates:
            return
        idx = max(
            0, min(self._app._mention_highlight, len(self._app._mention_candidates) - 1)
        )
        self._app._mention_highlight = idx
        popup.highlighted = idx

    def apply_mention_candidate(self, index: int) -> None:
        if index < 0 or index >= len(self._app._mention_candidates):
            return
        cand = self._app._mention_candidates[index]
        inp = self._app.query_one("#input", ComposerTextArea)
        inp.text = apply_mention(inp.text, cand)
        self.record_mention_binding(cand)
        self._app._mention_candidates = []
        self._app.query_one("#mention_popup", OptionList).display = False
        inp.focus()

    def expand_file_mention(self, text: str) -> str | None:
        if not self._app._mention_candidates:
            self._app._mention_candidates = list_mention_candidates(
                text, self._app._config.cwd
            )
        if not self._app._mention_candidates:
            return None
        idx = max(0, min(self._app._mention_highlight, len(self._app._mention_candidates) - 1))
        return apply_mention(text, self._app._mention_candidates[idx])

    def on_text_area_changed(self) -> None:
        self.sync_mention_popup()
        if self._app._thread:
            self.schedule_composer_draft_save()

    def approval_pending(self) -> bool:
        return bool(self._app._state.pending_approval_summary)

    def composer_blocked(self) -> bool:
        return isinstance(
            self._app.screen,
            (ApprovalOverlayScreen, UserInputOverlayScreen, TranscriptOverlayScreen),
        )

    def sync_composer_blocked(self) -> None:
        inp = self._app.query_one("#input", ComposerTextArea)
        inp.read_only = self.composer_blocked()

    def refresh_chrome(self, *, include_context: bool | None = None) -> None:
        if include_context is None:
            include_context = True
        if self._app._slash_has_overrides():
            self._app._config = self._app._resolve_config()

        self._app.query_one("#header", Static).update(str(self._app._config.cwd))
        self._app.query_one("#mode_badge", Static).update(
            format_mode_badge(plan_mode=self._app._slash.plan_mode)
        )
        context_snapshot = None
        session_cost_usd: float | None = None
        last_turn_fallback = False
        if include_context and self._app._thread is not None:
            from agent.context_meter import build_context_snapshot

            context_snapshot = build_context_snapshot(
                self._app._config,
                self._app._thread,
                ctx_settings=self._app._config.context,
            )
        if self._app._thread is not None:
            summary = thread_cost_summary(self._app._thread)
            raw_cost = summary.get("estimated_cost_usd")
            if raw_cost:
                session_cost_usd = float(raw_cost)
            if self._app._thread.turns:
                last_turn_fallback = bool(self._app._thread.turns[-1].usage.fallback_used)
        self._app.query_one("#composer_meta", Static).update(
            format_composer_meta(
                config=self._app._config,
                model_short=self._app._active_model_short(),
                profile=self._app._active_model_profile_name(),
                plan_mode=self._app._slash.plan_mode,
                thread=self._app._thread,
                include_context=include_context,
                turn_running=self._app._turn_running,
                routing_note=self._app._state.routing_note if include_context else "",
                session_auto_approve=self._app._session_auto_approve,
                pending_approval=self.approval_pending(),
                memories_pending=self._app._memories_pending_count(),
                statusline_settings=self._app._tui_settings.statusline,
                context_snapshot=context_snapshot,
                session_cost_usd=session_cost_usd,
                last_turn_fallback=last_turn_fallback,
            )
        )
        footer_mode = resolve_footer_mode(
            pending_approval=self.approval_pending(),
            pending_user_input=bool(self._app._state.pending_user_input_question),
            turn_running=self._app._turn_running,
            threads_loading=self._app._threads_loading,
            screen_mode=self._app._mode,
            reverse_search_active=self._app._reverse_search_active,
            resume_highlight=self._app._mode == "resume"
            and bool(self._app._resume_preview_text),
        )
        self._app.query_one("#composer_footer", Static).update(
            format_footer(
                FooterProps(
                    mode=footer_mode,
                    app_version=self._app._app_version,
                    screen_mode=self._app._mode,
                    turn_running=self._app._turn_running,
                    threads_loading=self._app._threads_loading,
                    reverse_search_query=self._app._reverse_search_query,
                    reverse_search_match=self._app._reverse_search_match,
                    resume_preview=self._app._resume_preview_text,
                    terminal_width=self._app.size.width,
                    reduced_motion=self._app._tui_settings.reduced_motion,
                )
            )
        )
        preview = self._app.query_one("#resume_preview", Static)
        if self._app._mode == "resume" and self._app._resume_preview_text:
            preview.update(self._app._resume_preview_text)
            preview.display = True
        else:
            preview.display = False
        self.refresh_approval_banner()

    def refresh_approval_banner(self) -> None:
        banner = self._app.query_one("#approval_banner", Static)
        inp = self._app.query_one("#input", ComposerTextArea)
        bottom = self._app.query_one("#bottom_chrome")
        if self.approval_pending():
            if self._app._approval_overlay_open:
                banner.update("[dim]Approval dialog open — y/n/a/A or Esc[/dim]")
                banner.display = True
            else:
                source_path = infer_path_from_diff(self._app._state.pending_approval_diff)
                banner.update(
                    render_approval_banner_text(
                        self._app._state.pending_approval_summary or "",
                        diff_preview=self._app._state.pending_approval_diff,
                        tool_name=self._app._state.pending_approval_tool,
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
        self.sync_composer_blocked()

    def open_approval_overlay(self) -> None:
        if self._app._approval_overlay_open or not self.approval_pending():
            return
        if isinstance(self._app.screen, ApprovalOverlayScreen):
            return

        summary = self._app._state.pending_approval_summary or ""
        diff_preview = self._app._state.pending_approval_diff
        tool_name = self._app._state.pending_approval_tool
        source_path = infer_path_from_diff(diff_preview)

        def on_done(key: str | None) -> None:
            self._app._approval_overlay_open = False
            if key:
                self._app._approval_queue.put(key)
                if key in ("A", "a"):
                    self._app._session_auto_approve = True
                self._app._state.pending_approval_summary = None
                self._app._state.pending_approval_diff = None
                self._app._state.pending_approval_tool = None
            self.sync_composer_blocked()
            self._app.query_one("#input", ComposerTextArea).focus()
            self.refresh_approval_banner()
            self.refresh_chrome()

        self._app._approval_overlay_open = True
        self._app.push_screen(
            ApprovalOverlayScreen(
                summary=summary,
                diff_preview=diff_preview,
                tool_name=tool_name,
                source_path=source_path,
            ),
            on_done,
        )
        self.sync_composer_blocked()

    def open_user_input_overlay(
        self,
        *,
        question: str,
        options: list[str],
        allow_free_text: bool,
        question_index: int = 1,
        question_total: int = 1,
    ) -> None:
        if isinstance(self._app.screen, UserInputOverlayScreen):
            return

        def on_done(result: dict[str, str | None] | None) -> None:
            if result is None:
                self._app._user_input_response_queue.put({"cancelled": "1"})
            else:
                self._app._user_input_response_queue.put(result)
            self.sync_composer_blocked()
            self._app.query_one("#input", ComposerTextArea).focus()
            self.refresh_chrome()

        self._app.push_screen(
            UserInputOverlayScreen(
                question=question,
                options=options or None,
                allow_free_text=allow_free_text,
                question_index=question_index,
                question_total=question_total,
            ),
            on_done,
        )
        self.sync_composer_blocked()

    def action_transcript_overlay(self) -> None:
        if self._app._mode == "chat":
            self._app.push_screen(
                TranscriptOverlayScreen(self._app._state),
                callback=self.on_overlay_closed,
            )
            self.sync_composer_blocked()

    def on_overlay_closed(self, _result: object | None = None) -> None:
        self.sync_composer_blocked()
        if self._app._mode == "chat":
            self.sync_transcript()
            self._app.query_one("#input", ComposerTextArea).focus()

    def action_toggle_expand(self) -> None:
        if self._app._transcript.toggle_last_expandable(self._app._state):
            self.sync_transcript()

    def on_cell_click(self, widget_id: str) -> None:
        if self._app._transcript.toggle_expand(self._app._state, widget_id):
            self.sync_transcript()

    def action_reverse_search(self) -> None:
        if self._app._reverse_search_active:
            return
        inp = self._app.query_one("#input", ComposerTextArea)
        self._app._reverse_search_saved_draft = inp.text
        self._app._reverse_search_active = True
        self._app._reverse_search_query = ""
        self._app._reverse_search_match = ""
        inp.read_only = True
        self.update_reverse_search()

    def action_reverse_search_cancel(self) -> None:
        if not self._app._reverse_search_active:
            return
        inp = self._app.query_one("#input", ComposerTextArea)
        self._app._reverse_search_active = False
        self._app._reverse_search_query = ""
        self._app._reverse_search_match = ""
        inp.read_only = False
        inp.text = self._app._reverse_search_saved_draft
        self._app._reverse_search_saved_draft = ""
        self.refresh_chrome()

    def action_reverse_search_accept(self) -> None:
        if not self._app._reverse_search_active:
            return
        match = self._app._reverse_search_match
        self.action_reverse_search_cancel()
        if match:
            self._app.query_one("#input", ComposerTextArea).text = match

    def update_reverse_search(self) -> None:
        self._app._reverse_search_match = (
            self._app._input_history.reverse_search(self._app._reverse_search_query)
            or ""
        )
        self.refresh_chrome()

    def clear_composer_on_submit(self) -> None:
        if self._app._thread:
            clear_composer_draft(self._app._config.cwd, self._app._thread.id)
        self._app._composer_bindings = []
