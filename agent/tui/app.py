from __future__ import annotations

import queue
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Label, ListItem, ListView, RichLog, Static

from agent.cancel import CancelToken, CancelledError
from agent.config import Config
from agent.profiles import merge_layered_config, apply_merged_to_resolve_kwargs, thread_cost_summary
from agent.events import AgentEvent
from agent.execution.factory import backend_display
from agent.git import detect_repo_root
from agent.models import Thread, new_id, utc_now_iso
from agent.recording.store import RunStore
from agent.store import ThreadStore
from agent.tui.runner import run_turn_in_thread
from agent.tui.view_model import (
    TuiState,
    apply_event_to_state,
    approval_key_to_response,
    filter_threads_by_cwd,
    thread_transcript_from_store,
    threads_to_entries,
)
from approval.gate import set_approval_input


class AgentTuiApp(App):
    TITLE = "agent-cli"
    CSS = """
    #threads { width: 30%; height: 1fr; border: solid green; }
    #transcript { width: 1fr; height: 1fr; border: solid blue; }
    #meta { height: 5; border: solid yellow; }
    #input { dock: bottom; height: 3; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_turn", "Cancel turn", show=True),
        Binding("ctrl+l", "clear_input", "Clear input", show=True),
        Binding("q", "quit_app", "Quit", show=True),
    ]

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        thread_id: str | None = None,
        resume_last: bool = False,
        profile: str | None = None,
        model_profile: str | None = None,
    ) -> None:
        super().__init__()
        resolved_cwd = (cwd or Path.cwd()).resolve()
        merged = merge_layered_config(
            resolved_cwd,
            cli_profile=profile,
            cli_model_profile=model_profile,
        )
        kwargs = apply_merged_to_resolve_kwargs(merged)
        self._config = Config.resolve(cwd=resolved_cwd, **kwargs)
        self._profile = profile or merged.get("profile", "")
        self._model_profile = model_profile or merged.get("model_profile", "")
        self._last_turn_cost: float | None = None
        self._config.require_api_key()
        self._store = ThreadStore()
        self._run_store = RunStore()
        self._thread: Thread | None = None
        self._requested_thread_id = thread_id
        self._resume_last = resume_last
        self._state = TuiState()
        self._cancel = CancelToken()
        self._worker: threading.Thread | None = None
        self._approval_queue: queue.Queue[str] = queue.Queue()
        self._turn_running = False
        self._thread_id_by_index: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="threads")
            with Vertical():
                yield RichLog(id="transcript", wrap=True, highlight=True)
                yield Static(id="meta")
        yield Input(placeholder="Enter task (Enter to submit)...", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self._load_threads()
        self._select_initial_thread()
        self._refresh_meta()
        self._render_transcript()

    def _load_threads(self) -> None:
        all_threads = self._store.list_threads()
        filtered = filter_threads_by_cwd(all_threads, self._config.cwd)
        self._state.threads = threads_to_entries(filtered)
        thread_list = self.query_one("#threads", ListView)
        thread_list.clear()
        self._thread_id_by_index.clear()
        for idx, entry in enumerate(self._state.threads):
            thread_list.append(ListItem(Label(entry.label)))
            self._thread_id_by_index[idx] = entry.thread_id

    def _select_initial_thread(self) -> None:
        if self._requested_thread_id:
            self._thread = self._resolve_thread(self._requested_thread_id)
        elif self._resume_last and self._state.threads:
            self._thread = self._store.load_thread(self._state.threads[0].thread_id)
        else:
            self._thread = None

        if self._thread:
            self._state.transcript = thread_transcript_from_store(self._thread)
            runs = self._run_store.list_runs(self._thread.id)
            if runs:
                events = self._run_store.load_events(
                    runs[0].turn_id, thread_id=self._thread.id
                )
                self._state = TuiState()
                for event in events:
                    self._state = apply_event_to_state(self._state, event)

    def _resolve_thread(self, thread_id: str) -> Thread:
        try:
            return self._store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [
                t
                for t in self._store.list_threads()
                if t.id.startswith(thread_id)
            ]
            if len(matches) == 1:
                return matches[0]
            raise

    def _refresh_meta(self) -> None:
        meta = self.query_one("#meta", Static)
        sandbox = self._config.sandbox_mode.value
        isolation = "on" if self._config.use_isolation else "off"
        backend = backend_display(self._config)
        thread_label = self._thread.display_label() if self._thread else "(new thread)"
        profile_bits = []
        if self._profile:
            profile_bits.append(f"profile={self._profile}")
        if self._model_profile:
            profile_bits.append(f"model-profile={self._model_profile}")
        profile_str = " | ".join(profile_bits)
        if profile_str:
            profile_str = f" | {profile_str}"
        cost_str = ""
        if self._last_turn_cost is not None:
            cost_str = f" | last cost≈${self._last_turn_cost:.4f}"
        elif self._thread:
            summary = thread_cost_summary(self._thread)
            if summary.get("estimated_cost_usd"):
                cost_str = f" | thread cost≈${summary['estimated_cost_usd']:.4f}"
        meta.update(
            f"cwd: {self._config.cwd.name} | model: {self._config.model}{profile_str}{cost_str} | "
            f"sandbox: {sandbox} | exec: {backend} | isolation: {isolation} | thread: {thread_label}"
        )

    def _render_transcript(self) -> None:
        log = self.query_one("#transcript", RichLog)
        log.clear()
        for line in self._state.transcript:
            prefix = line.role.upper()
            log.write(f"[bold]{prefix}:[/bold] {line.text}")
        if self._state.assistant_buffer:
            log.write(f"[bold]ASSISTANT:[/bold] {self._state.assistant_buffer}")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index not in self._thread_id_by_index:
            return
        thread_id = self._thread_id_by_index[index]
        self._thread = self._store.load_thread(thread_id)
        self._state.transcript = thread_transcript_from_store(self._thread)
        self._refresh_meta()
        self._render_transcript()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        if self._state.pending_approval_summary:
            response = approval_key_to_response(text)
            if response is None:
                self._append_system(f"Invalid approval key: {text!r} (use y/n/a/A)")
                return
            self._approval_queue.put(response)
            self._state.pending_approval_summary = None
            return

        if self._turn_running:
            self._append_system("Turn already running.")
            return

        if not self._thread:
            repo_root = detect_repo_root(self._config.cwd)
            self._thread = Thread(
                id=new_id(),
                cwd=str(self._config.cwd),
                model=self._config.model,
                repo_root=repo_root,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
            self._store.create_thread(self._thread)
            self._load_threads()
            self._refresh_meta()

        self._state.transcript.append(
            __import__(
                "agent.tui.view_model", fromlist=["TranscriptLine"]
            ).TranscriptLine(role="user", text=text)
        )
        self._render_transcript()
        self._start_turn(text)

    def _start_turn(self, prompt: str) -> None:
        assert self._thread is not None
        self._cancel = CancelToken()
        self._turn_running = True

        def on_event(event: AgentEvent) -> None:
            self.call_from_thread(self._handle_event, event)

        def worker() -> None:
            try:
                run_turn_in_thread(
                    self._thread,  # type: ignore[arg-type]
                    prompt,
                    self._config,
                    self._store,
                    on_event=on_event,
                    cancel_token=self._cancel,
                    approval_queue=self._approval_queue,
                )
            except CancelledError:
                pass
            finally:
                self.call_from_thread(self._turn_finished)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _handle_event(self, event: AgentEvent) -> None:
        self._state = apply_event_to_state(self._state, event)
        if event.type == "turn.completed" and self._thread:
            if self._thread.turns:
                last = self._thread.turns[-1]
                if last.usage.estimated_cost_usd:
                    self._last_turn_cost = last.usage.estimated_cost_usd
        self._render_transcript()

    def _turn_finished(self) -> None:
        self._turn_running = False
        if self._thread:
            try:
                self._thread = self._store.load_thread(self._thread.id)
            except FileNotFoundError:
                pass
        self._load_threads()
        self._refresh_meta()

    def _append_system(self, text: str) -> None:
        from agent.tui.view_model import TranscriptLine

        self._state.transcript.append(TranscriptLine(role="system", text=text))
        self._render_transcript()

    def action_cancel_turn(self) -> None:
        if self._turn_running:
            self._cancel.cancel()
            self._append_system("Cancelling turn...")

    def action_clear_input(self) -> None:
        self.query_one("#input", Input).value = ""

    def action_quit_app(self) -> None:
        if self._turn_running:
            self._append_system("Turn running — press Ctrl+C then q again to quit.")
            return
        self.exit()

    def on_unmount(self) -> None:
        set_approval_input(None)
