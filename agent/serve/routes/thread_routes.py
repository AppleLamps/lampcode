"""Thread, turn, approval, and SSE routes for agent serve."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent.config import Config
from agent.export.html import export_thread_html
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.multi_agent.checkpoint import CheckpointStore
from agent.serve.approvals import ApprovalRegistry, map_api_decision
from agent.serve.http_response import HttpResponseMixin, thread_to_dict
from agent.serve.request_limits import handle_body_error, read_limited_json
from agent.serve.turn_runner import TurnRunner
from agent.execution.sync.service import resolve_sync_path


class ThreadRoutesMixin(HttpResponseMixin):
    def _cancel_thread(self, thread_id: str) -> None:
        if not self._get_ctx().settings.enable_control:
            self._error(403, "Control disabled")
            return
        registry = ActiveTurnRegistry.global_registry()
        if not registry.cancel(thread_id):
            self._error(404, "No active turn for thread")
            return
        self._json_response({"ok": True, "thread_id": thread_id, "status": "cancelled"})

    def _sync_resolve(self) -> None:
        if not self._get_ctx().settings.enable_control:
            self._error(403, "Control disabled")
            return
        try:
            data = read_limited_json(self, max_bytes=self._get_ctx().settings.max_request_body_bytes)
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        if not isinstance(data, dict):
            self._error(400, "Invalid JSON")
            return
        rel_path = data.get("path", "")
        strategy = data.get("strategy", "local-wins")
        if not rel_path:
            self._error(400, "path required")
            return
        cwd = data.get("cwd")
        thread_id = data.get("thread_id")
        config = Config.resolve(cwd=Path(cwd) if cwd else None)
        config.execution.backend = "ssh"
        config.execution.ssh.sync_enabled = True
        result = resolve_sync_path(config, rel_path, strategy, thread_id=thread_id)
        status = 200 if result.get("ok") else 400
        self._json_response(result, status=status)

    def _start_turn(self, thread_id: str) -> None:
        ctx = self._get_ctx()
        if not ctx.settings.enable_control:
            self._error(403, "Control disabled")
            return
        if not ctx.settings.enable_turn_start:
            self._error(403, "Turn start disabled (enable_turn_start=false)")
            return
        try:
            data = read_limited_json(self, max_bytes=ctx.settings.max_request_body_bytes)
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        if not isinstance(data, dict):
            self._error(400, "Invalid JSON")
            return
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            self._error(400, "prompt required")
            return
        try:
            thread = self._load_thread(thread_id)
        except FileNotFoundError:
            self._error(404, "Thread not found")
            return
        config = Config.resolve(cwd=Path(thread.cwd))
        handle, err = TurnRunner.global_runner().start_turn(
            thread,
            prompt=prompt,
            config=config,
            store=ctx.store,
            settings=ctx.settings,
            run_store=ctx.run_store,
            extra=data,
        )
        if handle is None:
            self._error(429, err or "Unable to start turn")
            return
        self._json_response(
            {"turn_id": handle.turn_id or "pending", "status": handle.status, "thread_id": thread.id}
        )

    def _resolve_approval(self, approval_id: str) -> None:
        ctx = self._get_ctx()
        if not ctx.settings.enable_control:
            self._error(403, "Control disabled")
            return
        try:
            data = read_limited_json(self, max_bytes=ctx.settings.max_request_body_bytes)
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        if not isinstance(data, dict):
            self._error(400, "Invalid JSON")
            return
        decision = data.get("decision", "")
        if map_api_decision(decision) is None:
            self._error(400, "decision must be accept, deny, accept_turn, or accept_session")
            return
        if not ApprovalRegistry.global_registry().resolve(
            approval_id,
            decision,
            approved_by=self.principal.name if self.principal else None,
            approved_by_role=self.principal.role if self.principal else None,
        ):
            self._error(404, "Approval not found or already resolved")
            return
        self._json_response(
            {
                "ok": True,
                "approval_id": approval_id,
                "decision": decision,
                "approved_by": self.principal.name if self.principal else None,
                "approved_by_role": self.principal.role if self.principal else None,
            }
        )

    def _worker_graph(self, thread_id: str) -> None:
        try:
            thread = self._load_thread(thread_id)
        except FileNotFoundError:
            self._error(404, "Thread not found")
            return
        cfg = Config.resolve(cwd=Path(thread.cwd))
        cp = CheckpointStore(
            Path(cfg.multi_agent.checkpoint_dir).expanduser()
        ).find_latest(thread.id)
        if cp is None:
            self._json_response({"nodes": [], "edges": [], "status": "idle"})
            return
        nodes = [
            {
                "worker_id": w.worker_id,
                "status": w.status,
                "task": w.task,
                "attempts": w.attempts,
                "worker_dependencies": w.worker_dependencies,
                "error": w.error,
            }
            for w in cp.workers
        ]
        self._json_response(
            {
                "nodes": nodes,
                "edges": cp.edges,
                "status": cp.dag_status,
                "thread_id": thread.id,
                "turn_id": cp.turn_id,
            }
        )

    def _sse_thread_events(self, thread_id: str, *, turn_id: str | None = None) -> None:
        try:
            thread = self._load_thread(thread_id)
        except FileNotFoundError:
            self._error(404, "Thread not found")
            return
        if not thread.turns:
            self._error(404, "No turns")
            return
        stream_turn_id = turn_id
        if not stream_turn_id:
            stream_turn_id = thread.turns[-1].id if thread.turns else ""
        registry = ActiveTurnRegistry.global_registry()
        active_turn = registry.active_turn_id(thread.id)
        if active_turn:
            stream_turn_id = active_turn

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        seen = 0
        buf = self._get_ctx().settings.stream_buffer_size or 256
        for _ in range(buf):
            if not stream_turn_id:
                if active_turn := registry.active_turn_id(thread.id):
                    stream_turn_id = active_turn
                else:
                    time.sleep(0.2)
                    continue
            try:
                events = self._get_ctx().run_store.load_events(
                    stream_turn_id, thread_id=thread.id
                )
            except FileNotFoundError:
                events = []
            for event in events[seen:]:
                payload = event.to_json()
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            seen = len(events)
            if stream_turn_id and not registry.is_active(thread.id):
                break
            time.sleep(0.2)
