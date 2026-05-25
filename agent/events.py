from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

EventHandler = Callable[["AgentEvent"], None]


class EventBus:
    """Fan-out event bus for headless CLI, recording, and TUI."""

    def __init__(self, handlers: list[EventHandler] | None = None) -> None:
        self._handlers: list[EventHandler] = list(handlers or [])

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def emit(self, event: AgentEvent) -> None:
        for handler in self._handlers:
            handler(event)

    def as_handler(self) -> EventHandler:
        return self.emit


class RecordingHandler:
    """Persist every AgentEvent to ~/.agent-cli/runs/{thread}/{turn}.jsonl."""

    def __init__(
        self,
        store: "RunStore | None" = None,
        *,
        keep_last_runs_per_thread: int = 50,
    ) -> None:
        from agent.recording.store import RunStore

        self._store = store or RunStore()
        self._keep_last = keep_last_runs_per_thread
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def handle(self, event: AgentEvent) -> None:
        if not self._enabled or not event.thread_id or not event.turn_id:
            return
        self._store.append_event(event.thread_id, event.turn_id, event)
        if event.type == "turn.completed":
            self._store.prune_thread(event.thread_id, self._keep_last)


def build_event_emitter(
    *handlers: EventHandler,
    recording: bool = False,
    recording_keep: int = 50,
    run_store: "RunStore | None" = None,
) -> EventEmitter:
    bus = EventBus(list(handlers))
    if recording:
        from agent.recording.store import RunStore

        recorder = RecordingHandler(
            store=run_store or RunStore(),
            keep_last_runs_per_thread=recording_keep,
        )
        bus.subscribe(recorder.handle)
    return EventEmitter(bus.as_handler())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentEvent:
    type: str
    thread_id: str | None = None
    turn_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now)

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "thread_id": self.thread_id,
                "turn_id": self.turn_id,
                "data": self.data,
                "timestamp": self.timestamp,
            },
            ensure_ascii=False,
        )


class EventEmitter:
    def __init__(self, handler: EventHandler | None = None) -> None:
        self._handler = handler

    def emit(self, event: AgentEvent) -> None:
        if self._handler:
            self._handler(event)

    def turn_started(self, thread_id: str, turn_id: str) -> None:
        self.emit(AgentEvent("turn.started", thread_id=thread_id, turn_id=turn_id))

    def turn_completed(
        self,
        thread_id: str,
        turn_id: str,
        status: str,
        *,
        estimated_tokens: int | None = None,
    ) -> None:
        from agent.metrics import MetricsCollector

        if status == "completed":
            MetricsCollector.global_collector().inc("turns_completed")
            MetricsCollector.global_collector().inc_labeled("agent_turns_total", status)
        else:
            MetricsCollector.global_collector().inc_labeled("agent_turns_total", status)
        data: dict[str, Any] = {"status": status}
        if estimated_tokens is not None:
            data["estimated_tokens"] = estimated_tokens
        self.emit(
            AgentEvent(
                "turn.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data=data,
            )
        )

    def agent_delta(self, thread_id: str, turn_id: str, text: str) -> None:
        self.emit(
            AgentEvent(
                "agent.delta",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"text": text},
            )
        )

    def tool_pending(
        self, thread_id: str, turn_id: str, tool_name: str, arguments: dict[str, Any], *, source: str = "builtin"
    ) -> None:
        self.emit(
            AgentEvent(
                "tool.pending",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "arguments": arguments, "source": source},
            )
        )

    def approval_requested(
        self,
        thread_id: str,
        turn_id: str,
        tool_name: str,
        summary: str,
        *,
        approval_id: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"tool_name": tool_name, "summary": summary}
        if approval_id:
            data["approval_id"] = approval_id
        self.emit(
            AgentEvent(
                "approval.requested",
                thread_id=thread_id,
                turn_id=turn_id,
                data=data,
            )
        )

    def item_started(
        self, thread_id: str, turn_id: str, item_type: str, item_id: str
    ) -> None:
        self.emit(
            AgentEvent(
                "item.started",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"item_type": item_type, "item_id": item_id},
            )
        )

    def item_completed(
        self,
        thread_id: str,
        turn_id: str,
        item_type: str,
        item_id: str,
        status: str,
    ) -> None:
        self.emit(
            AgentEvent(
                "item.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"item_type": item_type, "item_id": item_id, "status": status},
            )
        )

    def compaction(self, thread_id: str, summarized_items: int) -> None:
        self.emit(
            AgentEvent(
                "compaction",
                thread_id=thread_id,
                data={"summarized_items": summarized_items},
            )
        )

    def compaction_completed(
        self,
        thread_id: str,
        *,
        removed_items: int,
        summary_chars: int,
        estimated_tokens_before: int,
        estimated_tokens_after: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "compaction.completed",
                thread_id=thread_id,
                data={
                    "removed_items": removed_items,
                    "summary_chars": summary_chars,
                    "estimated_tokens_before": estimated_tokens_before,
                    "estimated_tokens_after": estimated_tokens_after,
                },
            )
        )

    def sandbox_blocked(
        self,
        thread_id: str,
        turn_id: str,
        *,
        mode: str,
        reason: str,
        command: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.blocked",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "mode": mode,
                    "reason": reason,
                    "command": command,
                },
            )
        )

    def isolation_applied(
        self,
        thread_id: str,
        turn_id: str,
        *,
        pid: int | None,
        cwd: str,
        stripped_env_count: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "isolation.applied",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "pid": pid,
                    "cwd": cwd,
                    "stripped_env_count": stripped_env_count,
                },
            )
        )

    def execution_backend_selected(
        self,
        thread_id: str,
        turn_id: str,
        *,
        backend: str,
        image: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.backend.selected",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"backend": backend, "image": image},
            )
        )

    def execution_docker_started(
        self,
        thread_id: str,
        turn_id: str,
        *,
        image: str,
        container_id: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.docker.started",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"image": image, "container_id": container_id},
            )
        )

    def execution_docker_completed(
        self,
        thread_id: str,
        turn_id: str,
        *,
        image: str,
        exit_code: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.docker.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"image": image, "exit_code": exit_code},
            )
        )

    def execution_ssh_connected(
        self,
        thread_id: str,
        turn_id: str,
        *,
        host: str,
        user: str,
        remote_workspace: str,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.ssh.connected",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "host": host,
                    "user": user,
                    "remote_workspace": remote_workspace,
                },
            )
        )

    def execution_ssh_completed(
        self,
        thread_id: str,
        turn_id: str,
        *,
        host: str,
        exit_code: int,
        duration_ms: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.ssh.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "host": host,
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                },
            )
        )

    def execution_sync_started(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        direction: str,
        transport: str,
        bytes_estimated: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.sync.started",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "direction": direction,
                    "transport": transport,
                    "bytes_estimated": bytes_estimated,
                },
            )
        )

    def execution_sync_completed(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        direction: str,
        transport: str,
        files: int,
        bytes_transferred: int,
        duration_ms: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.sync.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "direction": direction,
                    "transport": transport,
                    "files": files,
                    "bytes": bytes_transferred,
                    "duration_ms": duration_ms,
                },
            )
        )

    def execution_sync_failed(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        reason: str,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.sync.failed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"reason": reason},
            )
        )

    def execution_sync_plan(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        counts: dict,
        conflicts: list[str] | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.sync.plan",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"counts": counts, "conflicts": conflicts or []},
            )
        )

    def execution_sync_remote_manifest_fetched(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        missing: bool,
        files: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.sync.remote_manifest_fetched",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"missing": missing, "files": files},
            )
        )

    def execution_sync_remote_scan_completed(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        files: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.sync.remote_scan_completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"files": files},
            )
        )

    def execution_ssh_pool_acquire(
        self, thread_id: str | None, *, host: str
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.ssh.pool.acquire",
                thread_id=thread_id,
                data={"host": host},
            )
        )

    def execution_ssh_pool_release(
        self, thread_id: str | None, *, host: str
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.ssh.pool.release",
                thread_id=thread_id,
                data={"host": host},
            )
        )

    def execution_docker_file_tool_applied(
        self,
        thread_id: str,
        turn_id: str,
        *,
        tool_name: str,
        path: str,
        image: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "execution.docker.file_tool_applied",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "path": path, "image": image},
            )
        )

    def collab_checkpoint_saved(
        self, thread_id: str, turn_id: str, *, path: str
    ) -> None:
        self.emit(
            AgentEvent(
                "collab.checkpoint.saved",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"path": path},
            )
        )

    def collab_spawn_started(
        self,
        thread_id: str,
        item_id: str,
        *,
        task: str,
    ) -> None:
        self.emit(
            AgentEvent(
                "collab.spawn.started",
                thread_id=thread_id,
                data={"item_id": item_id, "task": task},
            )
        )

    def collab_spawn_completed(
        self,
        thread_id: str,
        item_id: str,
        *,
        worker_thread_id: str,
        status: str,
        worker_id: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "collab.spawn.completed",
                thread_id=thread_id,
                data={
                    "item_id": item_id,
                    "worker_thread_id": worker_thread_id,
                    "status": status,
                    "worker_id": worker_id,
                },
            )
        )

    def multi_agent_dag_node_ready(
        self, thread_id: str, turn_id: str, worker_id: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.node_ready",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"worker_id": worker_id, **extra},
            )
        )

    def multi_agent_dag_node_started(
        self, thread_id: str, turn_id: str, worker_id: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.node_started",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"worker_id": worker_id, **extra},
            )
        )

    def multi_agent_dag_node_completed(
        self, thread_id: str, turn_id: str, worker_id: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.node_completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"worker_id": worker_id, **extra},
            )
        )

    def multi_agent_dag_blocked(
        self, thread_id: str, turn_id: str, worker_id: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.blocked",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"worker_id": worker_id, **extra},
            )
        )

    def multi_agent_dag_cycle_rejected(
        self, thread_id: str, turn_id: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.cycle_rejected",
                thread_id=thread_id,
                turn_id=turn_id,
                data=extra,
            )
        )

    def sandbox_profile_applied(
        self, thread_id: str | None, turn_id: str | None, *, profile: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.profile.applied",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"profile": profile, **extra},
            )
        )

    def sandbox_profile_skipped(
        self, thread_id: str | None, turn_id: str | None, *, reason: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.profile.skipped",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"reason": reason, **extra},
            )
        )

    def skills_marketplace_installed(
        self, name: str, *, version: str, publisher: str, scope: str
    ) -> None:
        self.emit(
            AgentEvent(
                "skills.marketplace.installed",
                data={"name": name, "version": version, "publisher": publisher, "scope": scope},
            )
        )

    def sandbox_kernel_selected(
        self, thread_id: str | None, turn_id: str | None, *, backend: str, mode: str
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.kernel.selected",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"backend": backend, "mode": mode},
            )
        )

    def sandbox_kernel_applied(
        self, thread_id: str | None, turn_id: str | None, *, backend: str, mode: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.kernel.applied",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"backend": backend, "mode": mode, **extra},
            )
        )

    def sandbox_kernel_fallback(
        self, thread_id: str | None, turn_id: str | None, *, reason: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.kernel.fallback",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"reason": reason, **extra},
            )
        )

    def multi_agent_program_linked(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        program_id: str,
        worker_id: str = "",
        **extra,
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.program.linked",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"program_id": program_id, "worker_id": worker_id, **extra},
            )
        )

    def multi_agent_program_node_completed(
        self,
        thread_id: str | None,
        turn_id: str | None,
        *,
        program_id: str,
        worker_id: str,
        status: str,
        **extra,
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.program.node_completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"program_id": program_id, "worker_id": worker_id, "status": status, **extra},
            )
        )

    def ide_file_write(
        self,
        thread_id: str | None,
        *,
        path: str,
        bytes_written: int,
        user: str,
        role: str,
        email: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "ide.file.write",
                thread_id=thread_id,
                data={
                    "path": path,
                    "bytes": bytes_written,
                    "user": user,
                    "email": email,
                    "role": role,
                },
            )
        )

    def multi_agent_dag_persisted(
        self, thread_id: str, turn_id: str, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.persisted",
                thread_id=thread_id,
                turn_id=turn_id,
                data=extra,
            )
        )

    def multi_agent_dag_resumed_across_turns(
        self, thread_id: str, turn_id: str, *, from_turn: str = "", **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.dag.resumed_across_turns",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"from_turn": from_turn, **extra},
            )
        )

    def multi_agent_budget_exceeded(
        self,
        thread_id: str,
        turn_id: str,
        *,
        metric: str,
        limit: float | int = 0,
        observed: float | int = 0,
        **extra,
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.budget.exceeded",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"metric": metric, "limit": limit, "observed": observed, **extra},
            )
        )

    def skills_marketplace_synced(
        self, *, skills: int = 0, cached: bool = False, **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "skills.marketplace.synced",
                data={"skills": skills, "cached": cached, **extra},
            )
        )

    def skills_marketplace_revocation_blocked(
        self, *, name: str, version: str, reason: str = "", **extra
    ) -> None:
        self.emit(
            AgentEvent(
                "skills.marketplace.revocation_blocked",
                data={"name": name, "version": version, "reason": reason, **extra},
            )
        )

    def auth_policy_denied(
        self,
        *,
        role: str,
        action: str,
        rule: str = "",
        message: str = "",
    ) -> None:
        self.emit(
            AgentEvent(
                "auth.policy.denied",
                data={"role": role, "action": action, "rule": rule, "message": message},
            )
        )

    def auth_session_revoked(
        self,
        *,
        session_id: str = "",
        reason: str = "",
    ) -> None:
        self.emit(
            AgentEvent(
                "auth.session.revoked",
                data={"session_id": session_id, "reason": reason},
            )
        )

    def schedule_job_started(self, job_id: str, **extra) -> None:
        self.emit(AgentEvent("schedule.job.started", data={"job_id": job_id, **extra}))

    def schedule_job_completed(self, job_id: str, **extra) -> None:
        self.emit(AgentEvent("schedule.job.completed", data={"job_id": job_id, **extra}))

    def schedule_job_failed(self, job_id: str, **extra) -> None:
        self.emit(AgentEvent("schedule.job.failed", data={"job_id": job_id, **extra}))

    def multi_agent_program_sync_conflict(
        self,
        *,
        program_id: str,
        node_ids: list[str] | None = None,
        **extra,
    ) -> None:
        self.emit(
            AgentEvent(
                "multi_agent.program.sync_conflict",
                data={"program_id": program_id, "node_ids": node_ids or [], **extra},
            )
        )

    def auth_webhook_received(self, *, event: str, subject: str = "", **extra) -> None:
        self.emit(
            AgentEvent(
                "auth.webhook.received",
                data={"event": event, "subject": subject, **extra},
            )
        )

    def auth_session_revoked_bulk(self, *, event: str = "", subject: str = "", count: int = 0, **extra) -> None:
        self.emit(
            AgentEvent(
                "auth.session.revoked_bulk",
                data={"event": event, "subject": subject, "count": count, **extra},
            )
        )

    def error(self, thread_id: str | None, message: str) -> None:
        self.emit(
            AgentEvent(
                "error",
                thread_id=thread_id,
                data={"message": message},
            )
        )

    def mcp_server_connected(self, thread_id: str | None, server: str) -> None:
        self.emit(
            AgentEvent(
                "mcp.server.connected",
                thread_id=thread_id,
                data={"server": server},
            )
        )

    def mcp_server_failed(self, thread_id: str | None, server: str, error: str) -> None:
        self.emit(
            AgentEvent(
                "mcp.server.failed",
                thread_id=thread_id,
                data={"server": server, "error": error},
            )
        )

    def project_rules_loaded(self, thread_id: str | None, path: str, char_count: int) -> None:
        self.emit(
            AgentEvent(
                "project.rules.loaded",
                thread_id=thread_id,
                data={"path": path, "char_count": char_count},
            )
        )

    def skill_activation(self, thread_id: str, turn_id: str, skills: list[str]) -> None:
        self.emit(
            AgentEvent(
                "skill.activation",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"skills": skills},
            )
        )

    def tool_completed(
        self,
        thread_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
        *,
        source: str = "builtin",
        diff_preview: str | None = None,
        summary: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"tool_name": tool_name, "status": status, "source": source}
        if diff_preview:
            data["diff_preview"] = diff_preview
        if summary:
            data["summary"] = summary
        self.emit(
            AgentEvent(
                "tool.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data=data,
            )
        )

    def approval_decided(
        self,
        thread_id: str,
        turn_id: str,
        *,
        tool_name: str,
        approved: bool,
    ) -> None:
        self.emit(
            AgentEvent(
                "approval.decided",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "approved": approved},
            )
        )

    def permission_escalated(
        self,
        thread_id: str,
        turn_id: str,
        *,
        scope: str,
        duration: str,
        reason: str = "",
    ) -> None:
        self.emit(
            AgentEvent(
                "permission.escalated",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"scope": scope, "duration": duration, "reason": reason},
            )
        )

    def permission_denied(
        self,
        thread_id: str,
        turn_id: str,
        *,
        scope: str,
        reason: str,
    ) -> None:
        self.emit(
            AgentEvent(
                "permission.denied",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"scope": scope, "reason": reason},
            )
        )

    def user_input(
        self,
        thread_id: str,
        turn_id: str,
        *,
        question: str,
        answer: str,
        selected_option: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "user.input",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "question": question,
                    "answer": answer,
                    "selected_option": selected_option,
                },
            )
        )

    def thread_started(self, thread_id: str, *, title: str | None = None) -> None:
        self.emit(
            AgentEvent(
                "thread.started",
                thread_id=thread_id,
                data={"title": title} if title else {},
            )
        )
