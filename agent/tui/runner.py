from __future__ import annotations

import queue
import signal
import threading
from pathlib import Path
from typing import Callable

from agent.cancel import CancelToken, CancelledError
from agent.config import Config
from agent.events import AgentEvent, EventEmitter, build_event_emitter
from agent.loop import run_turn
from agent.models import Thread, new_id, utc_now_iso
from agent.recording.replay import replay_events_to_lines
from agent.recording.store import RunStore
from agent.store import ThreadStore
from agent.tui.view_model import (
    TuiState,
    apply_event_to_state,
    filter_threads_by_cwd,
    thread_transcript_from_store,
    threads_to_entries,
)
from approval.gate import parse_approval_response, set_approval_input


def check_tui_available() -> tuple[bool, str]:
    try:
        import textual  # noqa: F401
    except ImportError:
        return False, "Textual is not installed. Run: pip install agent-cli[tui]"
    return True, ""


def launch_tui(
    *,
    cwd: Path | None = None,
    thread_id: str | None = None,
    resume_last: bool = False,
) -> None:
    ok, message = check_tui_available()
    if not ok:
        print(message)
        raise SystemExit(1)

    from agent.tui.app import AgentTuiApp

    app = AgentTuiApp(
        cwd=cwd,
        thread_id=thread_id,
        resume_last=resume_last,
    )
    app.run()


def run_turn_in_thread(
    thread: Thread,
    prompt: str,
    config: Config,
    store: ThreadStore,
    *,
    on_event: Callable[[AgentEvent], None],
    cancel_token: CancelToken,
    approval_queue: queue.Queue[str],
    session_auto_approve: bool = False,
) -> None:
    def approval_fn(summary: str) -> str:
        on_event(
            AgentEvent(
                "approval.requested",
                thread_id=thread.id,
                data={"tool_name": "", "summary": summary},
            )
        )
        while True:
            try:
                key = approval_queue.get(timeout=0.2)
            except queue.Empty:
                cancel_token.check()
                continue
            if key == "__cancel__":
                return "n"
            return key

    set_approval_input(approval_fn)
    emitter = build_event_emitter(
        on_event,
        recording=config.recording.enabled,
        recording_keep=config.recording.keep_last_runs_per_thread,
    )
    try:
        run_turn(
            thread,
            prompt,
            config,
            store,
            events=emitter,
            cancel_token=cancel_token,
            session_auto_approve=session_auto_approve,
        )
    finally:
        set_approval_input(None)


def load_thread_with_runs(
    thread: Thread,
    run_store: RunStore,
) -> TuiState:
    state = TuiState()
    state.transcript = thread_transcript_from_store(thread)
    runs = run_store.list_runs(thread.id)
    if runs:
        events = run_store.load_events(runs[0].turn_id, thread_id=thread.id)
        for line in replay_events_to_lines(events):
            if line.startswith("assistant:"):
                state.transcript.append(
                    __import__("agent.tui.view_model", fromlist=["TranscriptLine"]).TranscriptLine(
                        role="assistant",
                        text=line[len("assistant:") :].strip(),
                    )
                )
    return state
