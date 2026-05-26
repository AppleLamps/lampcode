from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, OptionList, RichLog, Static
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
from agent.threads_picker import thread_picker_label
from agent.tui.context_usage import compute_context_usage
from agent.tui.runner import run_turn_in_thread
from agent.tui.slash_commands import (
    TuiSlashState,
    execute_slash_command,
    parse_slash_command,
)
from agent.tui.theme import AGENT_LOGO, GROK_CSS, app_version, home_menu_options
from agent.tui.view_model import (
    TranscriptLine,
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
        Binding("escape", "back_to_home", "Back", show=False),
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
        self._mode: Literal["home", "chat", "resume"] = "home"
        self._session_threads: list[Thread] = []
        self._slash = TuiSlashState()

    def _resolve_config(self) -> Config:
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
        with Vertical(id="main"):
            with Vertical(id="home_panel"):
                yield Static(AGENT_LOGO, id="logo")
                yield OptionList(id="home_menu")
                yield OptionList(id="resume_menu")
            yield RichLog(id="transcript", wrap=True, highlight=True, markup=True)
        yield Static(id="tip")
        with Horizontal(id="composer_row"):
            yield Input(placeholder=">", id="input")
            yield Static(id="status_badge")
        yield Static(id="footer_bar")

    def on_mount(self) -> None:
        self._session_threads = filter_session_threads(
            self._store.list_threads(), self._config.cwd
        )
        self._select_initial_thread()
        self._refresh_chrome()
        if self._thread and self._state.transcript:
            self._set_mode("chat")
        elif self._resume_last and self._session_threads:
            self._load_thread(self._session_threads[0])
            self._set_mode("chat")
        else:
            self._set_mode("home")
        self.query_one("#input", Input).focus()

    def _refresh_chrome(self) -> None:
        self._config = self._resolve_config()
        self.query_one("#header", Static).update(str(self._config.cwd))
        sandbox = self._config.sandbox_mode.value
        model_short = self._config.model.split("/")[-1]
        plan_label = "plan on" if self._slash.plan_mode else "plan off"
        usage = compute_context_usage(self._config, self._thread)
        self.query_one("#status_badge", Static).update(
            f"{model_short} · {sandbox} · {plan_label}{usage.status_suffix()}"
        )
        tip = (
            "Tip: /model · /plan · /compact · /help — or Ctrl+S to resume."
            if self._mode != "home"
            else "Tip: Describe a task below, or press Ctrl+S to resume a session."
        )
        self.query_one("#tip", Static).update(tip)
        self.query_one("#footer_bar", Static).update(
            f"{usage.footer_label()} · agent {app_version()} · Ctrl+W new · Ctrl+Q quit"
        )

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
        transcript = self.query_one("#transcript", RichLog)
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
                    Option(thread_picker_label(thread), id=thread.id)
                )
            resume_menu.add_option(Option("← Back", id="__back__"))
            return

        home.display = False
        transcript.display = True
        home_menu.display = False
        resume_menu.display = False
        self._render_transcript()

    def _select_initial_thread(self) -> None:
        if self._requested_thread_id:
            self._thread = self._resolve_thread(self._requested_thread_id)
            if self._thread:
                self._state.transcript = thread_transcript_from_store(self._thread)
                runs = self._run_store.list_runs(self._thread.id)
                if runs:
                    self._state = TuiState()
                    for event in self._run_store.load_events(
                        runs[0].turn_id, thread_id=self._thread.id
                    ):
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

    def _load_thread(self, thread: Thread) -> None:
        self._thread = thread
        self._state = TuiState()
        self._state.transcript = thread_transcript_from_store(thread)
        summary = thread_cost_summary(thread)
        if summary.get("estimated_cost_usd"):
            self._last_turn_cost = float(summary["estimated_cost_usd"])

    def _render_transcript(self) -> None:
        log = self.query_one("#transcript", RichLog)
        log.clear()
        for line in self._state.transcript:
            self._write_transcript_line(log, line)
        if self._state.assistant_buffer:
            log.write(self._state.assistant_buffer)

    def _write_transcript_line(self, log: RichLog, line: TranscriptLine) -> None:
        if line.role == "user":
            log.write(f"\n[dim]You[/dim]\n{line.text}")
        elif line.role == "assistant":
            log.write(f"\n{line.text}")
        elif line.role == "approval":
            log.write(f"\n[yellow]⚠ {line.text}[/yellow]")
        elif line.role == "tool":
            log.write(f"\n[dim italic]▸ {line.text}[/dim italic]")
        else:
            log.write(f"\n[dim]{line.text}[/dim]")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = str(event.option_id or "")
        widget_id = event.option_list.id

        if widget_id == "home_menu":
            if option_id == "new":
                self.query_one("#input", Input).focus()
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
            self.query_one("#input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        if self._state.pending_approval_summary:
            response = approval_key_to_response(text)
            if response is None:
                self._append_system(f"Invalid key {text!r} — use y, n, a, or A")
                return
            self._approval_queue.put(response)
            self._state.pending_approval_summary = None
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

        self._state.transcript.append(TranscriptLine(role="user", text=text))
        self._render_transcript()
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
            self._store.list_threads(), self._config.cwd
        )

    def _handle_slash_command(self, name: str, arg: str) -> None:
        if self._turn_running and name not in ("/help", "/model", "/plan", "/profile", "/quit", "/exit", "/q"):
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
            self._last_turn_cost = None
            if result.enter_chat:
                self._set_mode("chat")
            else:
                self._set_mode("home")

        if result.reload_thread and self._thread:
            try:
                self._thread = self._store.load_thread(self._thread.id)
                self._state.transcript = thread_transcript_from_store(self._thread)
            except FileNotFoundError:
                pass
            if self._mode == "chat":
                self._render_transcript()

        if result.message:
            if self._mode == "home" and not result.enter_chat:
                self.query_one("#tip", Static).update(result.message)
            else:
                if self._mode == "home":
                    self._set_mode("chat")
                self._append_system(result.message)

        self._refresh_chrome()

    def _start_turn(self, prompt: str) -> None:
        assert self._thread is not None
        config = self._resolve_config()
        self._thread.model = config.model
        self._cancel = CancelToken()
        self._turn_running = True
        self._refresh_chrome()

        def on_event(agent_event: AgentEvent) -> None:
            self.call_from_thread(self._handle_event, agent_event)

        def worker() -> None:
            try:
                run_turn_in_thread(
                    self._thread,  # type: ignore[arg-type]
                    prompt,
                    config,
                    self._store,
                    on_event=on_event,
                    cancel_token=self._cancel,
                    approval_queue=self._approval_queue,
                    plan_mode=self._slash.plan_mode,
                )
            except CancelledError:
                pass
            finally:
                self.call_from_thread(self._turn_finished)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _handle_event(self, event: AgentEvent) -> None:
        self._state = apply_event_to_state(self._state, event)
        if event.type == "turn.completed" and self._thread and self._thread.turns:
            last = self._thread.turns[-1]
            if last.usage.estimated_cost_usd:
                self._last_turn_cost = last.usage.estimated_cost_usd
        self._render_transcript()
        self._refresh_chrome()

    def _turn_finished(self) -> None:
        self._turn_running = False
        if self._thread:
            try:
                self._thread = self._store.load_thread(self._thread.id)
            except FileNotFoundError:
                pass
        self._session_threads = filter_session_threads(
            self._store.list_threads(), self._config.cwd
        )
        self._refresh_chrome()

    def _append_system(self, text: str) -> None:
        self._state.transcript.append(TranscriptLine(role="system", text=text))
        if self._mode == "chat":
            self._render_transcript()

    def action_new_session(self) -> None:
        if self._turn_running:
            self._append_system("Wait for the current turn to finish.")
            return
        self._thread = None
        self._state = TuiState()
        self._last_turn_cost = None
        self._slash = TuiSlashState()
        self._set_mode("home")
        self.query_one("#input", Input).focus()

    def action_resume_session(self) -> None:
        if self._turn_running:
            self._append_system("Wait for the current turn to finish.")
            return
        self._session_threads = filter_session_threads(
            self._store.list_threads(), self._config.cwd
        )
        if not self._session_threads:
            self._set_mode("home")
            self.query_one("#tip", Static).update(
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

    def action_quit_app(self) -> None:
        if self._turn_running:
            self._append_system("Turn running — cancel with Ctrl+C first.")
            return
        self.exit()

    def on_unmount(self) -> None:
        set_approval_input(None)
