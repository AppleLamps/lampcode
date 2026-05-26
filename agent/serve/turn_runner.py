from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from agent.cancel import CancelToken
from agent.config import Config
from agent.events import EventEmitter, build_event_emitter
from agent.harness.active_turns import ActiveTurnRegistry
from agent.logging import log_info
from agent.loop import run_turn
from agent.metrics import MetricsCollector
from agent.models import Thread
from agent.recording.store import RunStore
from agent.session import HarnessSession
from agent.settings import ServeSettings
from agent.store import ThreadStore
from approval.gate import HttpApprovalBridge, set_http_approval_bridge


@dataclass
class TurnRunHandle:
    thread_id: str
    turn_id: str = ""
    status: str = "started"


class TurnRunner:
    _instance: TurnRunner | None = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    @classmethod
    def global_runner(cls) -> TurnRunner:
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._threads.values() if t.is_alive())

    def start_turn(
        self,
        thread: Thread,
        *,
        prompt: str,
        config: Config,
        store: ThreadStore,
        settings: ServeSettings,
        run_store: RunStore | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[TurnRunHandle | None, str | None]:
        if settings.max_concurrent_turns > 0 and self.active_count() >= settings.max_concurrent_turns:
            return None, f"max concurrent turns ({settings.max_concurrent_turns}) reached"
        if ActiveTurnRegistry.global_registry().is_active(thread.id):
            return None, "thread already has an active turn"

        extra = extra or {}
        if extra.get("model"):
            config.model = str(extra["model"])
            thread.model = config.model
        if extra.get("execution_backend"):
            config.execution.backend = str(extra["execution_backend"])
        if extra.get("sync_mode"):
            config.execution.ssh.sync_mode = str(extra["sync_mode"])
            config.execution.ssh.sync_enabled = True
        auto_approve = bool(extra.get("auto_approve", False))
        if auto_approve:
            config.auto_approve = True

        handle = TurnRunHandle(thread_id=thread.id)
        holder: dict[str, str] = {}

        def _run() -> None:
            events: list = []

            def _collect(event: AgentEvent) -> None:
                events.append(event)

            base_emitter = EventEmitter(_collect)
            cfg = Config.resolve()
            emitter = build_event_emitter(
                _collect,
                recording=True,
                run_store=run_store or RunStore(),
                action_log=cfg.action_log,
                project_cwd=cfg.cwd,
                model=cfg.model,
            )

            def _emit_approval(approval_id: str, tool_name: str, summary: str) -> None:
                emitter.approval_requested(
                    thread.id,
                    holder.get("turn_id", ""),
                    tool_name,
                    summary,
                    approval_id=approval_id,
                )

            bridge = HttpApprovalBridge(
                thread_id=thread.id,
                turn_id="",
                timeout_sec=settings.approval_timeout_sec,
                emit=_emit_approval,
            )
            set_http_approval_bridge(bridge)
            MetricsCollector.global_collector().inc("http_turns_started")
            MetricsCollector.global_collector().adjust_gauge("http_turns_active", 1)
            log_info("turn.start", thread_id=thread.id, message=prompt[:80])
            try:
                turn = run_turn(
                    thread,
                    prompt,
                    config,
                    store,
                    events=emitter,
                    cancel_token=CancelToken(),
                    session_auto_approve=auto_approve,
                    harness_session=HarnessSession(),
                )
                handle.turn_id = turn.id
                handle.status = turn.status
                holder["turn_id"] = turn.id
                bridge.turn_id = turn.id
            except Exception as exc:
                handle.status = "failed"
                log_info("turn.failed", thread_id=thread.id, message=str(exc))
            finally:
                set_http_approval_bridge(None)
                MetricsCollector.global_collector().adjust_gauge("http_turns_active", -1)
                with self._lock:
                    self._threads.pop(thread.id, None)

        t = threading.Thread(target=_run, daemon=True)
        with self._lock:
            self._threads[thread.id] = t
        t.start()
        return handle, None
