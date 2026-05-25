from __future__ import annotations

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent.config import Config
from agent.export.html import export_thread_html, render_index_html
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.models import Thread
from agent.multi_agent.checkpoint import CheckpointStore
from agent.recording.store import RunStore
from agent.serve.auth import authorize_request
from agent.settings import ServeSettings
from agent.store import ThreadStore


class ServeContext:
    def __init__(
        self,
        *,
        store: ThreadStore,
        run_store: RunStore,
        settings: ServeSettings,
        auth_token: str,
    ) -> None:
        self.store = store
        self.run_store = run_store
        self.settings = settings
        self.auth_token = auth_token


class AgentHttpHandler(BaseHTTPRequestHandler):
    ctx: ServeContext | None = None
    store: ThreadStore | None = None
    run_store: RunStore | None = None

    def _get_ctx(self) -> ServeContext:
        if self.ctx is not None:
            return self.ctx
        return ServeContext(
            store=self.store or ThreadStore(),
            run_store=self.run_store or RunStore(),
            settings=ServeSettings(),
            auth_token="",
        )

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if not self._authorize():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        if path == "/metrics":
            self._metrics_response()
            return

        if path == "/":
            threads = self._get_ctx().store.list_threads()
            active = sum(
                1 for t in threads if ActiveTurnRegistry.global_registry().is_active(t.id)
            )
            html = render_index_html(threads, active_turns=active)
            self._html_response(html)
            return

        if path == "/threads":
            threads = self._get_ctx().store.list_threads()
            data = [
                {
                    "id": t.id,
                    "title": t.title,
                    "cwd": t.cwd,
                    "updated_at": t.updated_at,
                    "label": t.display_label(),
                    "active": ActiveTurnRegistry.global_registry().is_active(t.id),
                }
                for t in threads
            ]
            self._json_response(data)
            return

        if path.startswith("/threads/") and path.endswith("/events"):
            thread_id = path[len("/threads/") : -len("/events")]
            self._sse_thread_events(thread_id)
            return

        if path.startswith("/threads/"):
            thread_id = path.split("/threads/", 1)[1]
            suffix = ""
            if thread_id.endswith(".html"):
                thread_id = thread_id[: -len(".html")]
                suffix = ".html"
            try:
                thread = self._load_thread(thread_id)
            except FileNotFoundError:
                self._error(404, "Thread not found")
                return
            wants_html = suffix == ".html" or "text/html" in self.headers.get("Accept", "")
            if wants_html:
                cfg = Config.resolve(cwd=Path(thread.cwd))
                checkpoint = CheckpointStore(
                    Path(cfg.multi_agent.checkpoint_dir).expanduser()
                ).find_latest(thread.id)
                self._html_response(
                    export_thread_html(
                        thread,
                        sandbox=cfg.sandbox_mode.value,
                        backend=cfg.execution.backend,
                        checkpoint=checkpoint,
                    )
                )
                return
            self._json_response(_thread_to_dict(thread))
            return

        if path.startswith("/runs/"):
            turn_id = path.split("/runs/", 1)[1]
            try:
                events = self._get_ctx().run_store.load_events(turn_id)
            except FileNotFoundError:
                self._error(404, "Run not found")
                return
            self._json_response([json.loads(e.to_json()) for e in events])
            return

        self._error(404, "Not found")

    def do_POST(self) -> None:
        if not self._authorize():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        if path.startswith("/threads/") and path.endswith("/cancel"):
            thread_id = path[len("/threads/") : -len("/cancel")]
            self._cancel_thread(thread_id)
            return

        if path == "/sync/resolve":
            self._sync_resolve()
            return

        self._error(404, "Not found")

    def _authorize(self) -> bool:
        ctx = self._get_ctx()
        ok, err = authorize_request(
            self.path,
            dict(self.headers),
            auth_token=ctx.auth_token,
        )
        if not ok:
            self._error(401, err or "Unauthorized")
            return False
        return True

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
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "Invalid JSON")
            return
        rel_path = data.get("path", "")
        strategy = data.get("strategy", "local-wins")
        if not rel_path:
            self._error(400, "path required")
            return
        self._json_response({"ok": True, "path": rel_path, "strategy": strategy})

    def _sse_thread_events(self, thread_id: str) -> None:
        try:
            thread = self._load_thread(thread_id)
        except FileNotFoundError:
            self._error(404, "Thread not found")
            return
        if not thread.turns:
            self._error(404, "No turns")
            return
        turn_id = thread.turns[-1].id
        registry = ActiveTurnRegistry.global_registry()
        active_turn = registry.active_turn_id(thread.id)
        if active_turn:
            turn_id = active_turn

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        seen = 0
        for _ in range(30):
            try:
                events = self._get_ctx().run_store.load_events(turn_id, thread_id=thread.id)
            except FileNotFoundError:
                events = []
            for event in events[seen:]:
                payload = event.to_json()
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            seen = len(events)
            if not registry.is_active(thread.id):
                break
            time.sleep(0.2)

    def _metrics_response(self) -> None:
        snap = MetricsCollector.global_collector().snapshot()
        self._json_response({"counters": snap.counters, "gauges": snap.gauges})

    def _load_thread(self, thread_id: str) -> Thread:
        ctx = self._get_ctx()
        try:
            return ctx.store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [
                t
                for t in ctx.store.list_threads()
                if t.id.startswith(thread_id) or t.id == thread_id
            ]
            if len(matches) == 1:
                return matches[0]
            raise

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self._get_ctx().settings.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json_response({"error": message}, status=status)


def _thread_to_dict(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "cwd": thread.cwd,
        "model": thread.model,
        "title": thread.title,
        "forked_from": thread.forked_from,
        "active": ActiveTurnRegistry.global_registry().is_active(thread.id),
        "turns": [
            {
                "id": turn.id,
                "status": turn.status,
                "items": [item.model_dump() for item in turn.items],
            }
            for turn in thread.turns
        ],
    }


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    settings: ServeSettings | None = None,
    auth_token: str | None = None,
) -> None:
    store = ThreadStore()
    run_store = RunStore()
    cfg = settings or ServeSettings(host=host, port=port)
    token = auth_token if auth_token is not None else cfg.auth_token
    if not token:
        token = secrets.token_urlsafe(24)
        print(f"[agent serve] generated auth token: {token}")

    ctx = ServeContext(store=store, run_store=run_store, settings=cfg, auth_token=token)

    class Handler(AgentHttpHandler):
        pass

    Handler.ctx = ctx
    Handler.store = store
    Handler.run_store = run_store

    bind_host = host or cfg.host
    bind_port = port or cfg.port
    server = ThreadingHTTPServer((bind_host, bind_port), Handler)
    print(f"agent serve listening on http://{bind_host}:{bind_port}")
    print(f"  Auth: Bearer token required" if token else "  Auth: disabled")
    print("  GET /              HTML thread list")
    print("  GET /threads       JSON thread list")
    print("  GET /threads/{id}/events  SSE event stream")
    print("  GET /metrics       JSON metrics")
    print("  POST /threads/{id}/cancel  Cancel active turn")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
