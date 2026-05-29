"""Agent turn execution, events, and slash-command handling."""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

from textual.widgets import Static

from agent.cancel import CancelToken, CancelledError
from agent.tui.composer import ComposerTextArea
from agent.events import AgentEvent
from agent.git import detect_repo_root
from agent.models import Thread, new_id, utc_now_iso
from agent.tui.cells.base import SystemCell, UserMessageCell
from agent.tui.messages import AgentEventMessage, WorkerErrorMessage, WorkerFinishedMessage
from agent.tui.runner import run_turn_in_thread
from agent.tui.slash_commands import execute_slash_command, parse_slash_command
from agent.tui.stream_coalesce import STREAM_SYNC_INTERVAL_SEC
from agent.tui.view_model import (
    TuiState,
    approval_key_to_response,
    apply_event_to_state,
    filter_session_threads,
    thread_transcript_from_store,
)

if TYPE_CHECKING:
    from agent.tui.app import AgentTuiApp


class TurnController:
    def __init__(self, app: AgentTuiApp) -> None:
        self._app = app
        self._stream_coalesce_timer = None

    def _cancel_stream_coalesce(self) -> None:
        timer = self._stream_coalesce_timer
        self._stream_coalesce_timer = None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _enqueue_event(self, agent_event: AgentEvent) -> None:
        self._app._event_queue.put(agent_event)
        if agent_event.type != "agent.delta":
            self._cancel_stream_coalesce()
            self._app.post_message(AgentEventMessage())
            return
        if self._stream_coalesce_timer is not None:
            return
        self._stream_coalesce_timer = self._app.call_later(
            STREAM_SYNC_INTERVAL_SEC,
            self._on_stream_coalesce,
        )

    def _on_stream_coalesce(self) -> None:
        self._stream_coalesce_timer = None
        self._app.post_message(AgentEventMessage())

    def submit_input(self, raw_text: str) -> None:
        text = raw_text.strip()
        if not text:
            return
        inp = self._app.query_one("#input", ComposerTextArea)
        inp.clear()
        inp.reset_history_navigation()

        if self._app._reverse_search_active:
            return

        if self._app._chat.approval_pending():
            response = approval_key_to_response(text)
            if response is None:
                self.append_system(f"Invalid key {text!r} — use y, n, a, or A")
                return
            self._app._approval_queue.put(response)
            if response in ("A", "a"):
                self._app._session_auto_approve = True
            self._app._state.pending_approval_summary = None
            self._app._state.pending_approval_diff = None
            self._app._state.pending_approval_tool = None
            self._app._chat.refresh_approval_banner()
            self._app._chat.refresh_chrome()
            return

        parsed = parse_slash_command(text)
        if parsed is not None:
            self.handle_slash_command(*parsed)
            return

        if self._app._turn_running:
            self.append_system("Turn already running — Ctrl+C to cancel.")
            return

        if self._app._mode == "home":
            self._app._home.set_mode("chat")

        if not self._app._thread:
            self.ensure_thread()

        self._app._chat.clear_composer_on_submit()
        self._app._input_history.add(text)
        self._app._state.transcript.append(UserMessageCell(text=text))
        self._app._chat.sync_transcript()
        self.start_turn(text)

    def ensure_thread(self) -> None:
        if self._app._thread:
            return
        repo_root = detect_repo_root(self._app._config.cwd)
        config = self._app._resolve_config()
        self._app._thread = Thread(
            id=new_id(),
            cwd=str(self._app._config.cwd),
            model=config.model,
            repo_root=repo_root,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        self._app._store.create_thread(self._app._thread)
        self._app._session_threads = filter_session_threads(
            self._app._store.list_thread_meta(), self._app._config.cwd
        )

    def handle_slash_command(self, name: str, arg: str) -> None:
        if name == "/expand":
            if self._app._transcript.toggle_last_expandable(self._app._state):
                self._app._chat.sync_transcript()
            return

        if self._app._turn_running and name not in (
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
            self.append_system("Wait for the current turn to finish.")
            return

        result = execute_slash_command(
            name,
            arg,
            state=self._app._slash,
            config=self._app._resolve_config(),
            store=self._app._store,
            thread=self._app._thread,
        )
        if not result.handled:
            return

        if result.quit_app:
            self._app.action_quit_app()
            return

        if result.clear_session:
            self._app._thread = None
            self._app._state = TuiState()
            self._app._transcript.reset()
            self._app._last_turn_cost = None
            if result.enter_chat:
                self._app._home.set_mode("chat")
            else:
                self._app._home.set_mode("home")

        if result.reload_thread and self._app._thread:
            try:
                self._app._thread = self._app._store.load_thread(self._app._thread.id)
                self._app._state.transcript = thread_transcript_from_store(self._app._thread)
                self._app._transcript.reset()
            except FileNotFoundError:
                pass
            if self._app._mode == "chat":
                self._app._chat.sync_transcript(rebuild=True)

        if result.message:
            if self._app._mode == "home" and not result.enter_chat:
                self._app.query_one("#composer_footer", Static).update(result.message)
            else:
                if self._app._mode == "home":
                    self._app._home.set_mode("chat")
                self.append_system(result.message)

        self._app._chat.refresh_chrome()

    def start_turn(self, prompt: str) -> None:
        assert self._app._thread is not None
        config = self._app._resolve_config()
        self._app._thread.model = config.model
        self._app._state.turn_model = config.model.split("/")[-1]
        self._app._state.last_user_prompt = prompt
        self._app._state.turn_fallback_used = False
        self._app._state.turn_cost = None
        from agent.model_routing import resolve_model_profile_from_task

        profile = resolve_model_profile_from_task(
            prompt,
            cli_model_profile=self._app._slash.model_profile_override,
            cwd=config.cwd,
        )
        self._app._state.routing_note = f"profile={profile}" if profile else ""
        self._app._slash.last_routing_prompt = prompt
        self._app._cancel = CancelToken()
        self._app._turn_running = True
        if self._app._status_row:
            if not self._app._status_row.interval_active:
                self._app._status_row.bind_interval(self._app)
            self._app._status_row.start_turn()
        self._app._chat.refresh_chrome()

        def on_event(agent_event: AgentEvent) -> None:
            self._enqueue_event(agent_event)

        def worker() -> None:
            worker_error: str | None = None
            try:
                run_turn_in_thread(
                    self._app._thread,  # type: ignore[arg-type]
                    prompt,
                    config,
                    self._app._store,
                    on_event=on_event,
                    cancel_token=self._app._cancel,
                    approval_queue=self._app._approval_queue,
                    user_input_response_queue=self._app._user_input_response_queue,
                    session_auto_approve=self._app._session_auto_approve,
                    plan_mode=self._app._slash.plan_mode,
                )
            except CancelledError:
                pass
            except Exception as exc:
                worker_error = str(exc)
            if worker_error:
                self._app.post_message(WorkerErrorMessage(worker_error))
            self._app.post_message(WorkerFinishedMessage())

        self._app._worker = threading.Thread(target=worker, daemon=True)
        self._app._worker.start()

    def drain_event_queue(self) -> None:
        events: list[AgentEvent] = []
        while True:
            try:
                events.append(self._app._event_queue.get_nowait())
            except queue.Empty:
                break
        if not events:
            return
        all_deltas = all(event.type == "agent.delta" for event in events)
        for index, event in enumerate(events):
            sync = (not all_deltas) or index == len(events) - 1
            self.handle_event(
                event,
                sync=sync,
                stream_only=all_deltas and sync,
            )

    def handle_event(
        self,
        event: AgentEvent,
        *,
        sync: bool = True,
        stream_only: bool = False,
    ) -> None:
        if event.type == "tool.pending" and self._app._mode == "chat":
            scroll, cells = self._app._chat.transcript_targets()
            self._app._transcript.finalize_assistant_stream(
                cells, self._app._state, scroll=scroll
            )

        self._app._state = apply_event_to_state(self._app._state, event)

        if event.type == "turn.completed" and self._app._thread and self._app._thread.turns:
            last = self._app._thread.turns[-1]
            if last.usage.estimated_cost_usd:
                self._app._last_turn_cost = last.usage.estimated_cost_usd
                self._app._state.turn_cost = last.usage.estimated_cost_usd
            self._app._state.turn_fallback_used = bool(last.usage.fallback_used)
            if last.usage.model_used:
                self._app._state.turn_model = last.usage.model_used.split("/")[-1]

        if event.type == "turn.completed" and self._app._mode == "chat":
            self._app._transcript.commit_orphan_live_stream(self._app._state)
            self._app._transcript.clear_live_stream_state()

        if event.type == "user_input.requested":
            self._app._chat.open_user_input_overlay(
                question=str(event.data.get("question", "")),
                options=list(event.data.get("options") or []),
                allow_free_text=bool(event.data.get("allow_free_text", True)),
                question_index=int(event.data.get("question_index", 1) or 1),
                question_total=int(event.data.get("question_total", 1) or 1),
            )

        if event.type == "approval.requested":
            self._app._chat.open_approval_overlay()

        if sync:
            if stream_only or event.type == "agent.delta":
                self._app._chat.sync_transcript(stream_only=True)
            else:
                self._app._chat.sync_transcript()
            include_context = event.type not in (
                "agent.delta",
                "agent.reasoning",
                "tool.executing",
            )
            self._app._chat.refresh_chrome(include_context=include_context)

        if self._app._status_row:
            if event.type == "tool.executing":
                self._app._status_row.set_detail(self._app._state.status_detail)
            elif event.type in ("tool.completed", "turn.completed", "turn.started"):
                self._app._status_row.set_detail("")

    def watch_turn_worker(self) -> None:
        if not self._app._turn_running:
            return
        if self._app._worker is not None and self._app._worker.is_alive():
            return
        self.append_system(
            "Turn worker stopped unexpectedly (UI was still waiting). "
            "Check ~/.agent-cli/logs/ for the last action."
        )
        self.turn_finished()

    def turn_finished(self) -> None:
        self._cancel_stream_coalesce()
        self._app._turn_running = False
        if self._app._reflow.resize_during_stream:
            self._app._chat.schedule_transcript_reflow()
        if self._app._status_row:
            self._app._status_row.stop_turn(self._app._state.status_line)
        if self._app._thread:
            try:
                self._app._thread = self._app._store.load_thread(self._app._thread.id)
            except FileNotFoundError:
                pass
            self.enrich_turn_summary_from_thread()
        self._app._session_threads = filter_session_threads(
            self._app._store.list_thread_meta(), self._app._config.cwd
        )
        self._app._chat.refresh_chrome()

    def enrich_turn_summary_from_thread(self) -> None:
        from agent.turn_stats import aggregate_turn_stats
        from agent.tui.cells.base import TurnSummaryCell

        if not self._app._thread or not self._app._thread.turns:
            return
        stats = aggregate_turn_stats(self._app._thread.turns[-1])
        for cell in reversed(self._app._state.transcript):
            if isinstance(cell, TurnSummaryCell):
                cell.files_changed = stats.files_touched
                cell.lines_added = stats.lines_added
                cell.lines_removed = stats.lines_removed
                cell.commands_run = stats.commands_run
                last_usage = self._app._thread.turns[-1].usage
                if last_usage.model_used:
                    cell.model = last_usage.model_used.split("/")[-1]
                cell.fallback_used = bool(last_usage.fallback_used)
                if self._app._state.routing_note:
                    cell.routing_note = self._app._state.routing_note
                break
        if self._app._mode == "chat":
            self._app._chat.sync_transcript()

    def append_system(self, text: str) -> None:
        self._app._state.transcript.append(SystemCell(text=text))
        if self._app._mode == "chat":
            self._app._chat.sync_transcript()
        else:
            self._app.query_one("#composer_footer", Static).update(f"[yellow]{text}[/yellow]")

    def on_worker_error(self, message: str) -> None:
        from agent.tui.cells.base import ErrorCell

        if self._app._mode == "home":
            self._app._home.set_mode("chat")
        self._app._state.transcript.append(ErrorCell(message=message, severity="error"))
        self._app._chat.sync_transcript(rebuild=True)

    def action_cancel_turn(self) -> None:
        if self._app._turn_running:
            self._app._cancel.cancel()
            self.append_system("Cancelling…")

    def action_why_model(self) -> None:
        from agent.model_routing import explain_model_routing

        task = self._app._slash.last_routing_prompt or self._app._state.last_user_prompt
        if not task:
            self.append_system("No prompt to analyze yet.")
            return
        self.append_system(
            explain_model_routing(
                task,
                cli_model_profile=self._app._slash.model_profile_override,
                cwd=self._app._config.cwd,
            )
        )
