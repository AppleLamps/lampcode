from __future__ import annotations

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent.config import Config
from agent.export.html import export_thread_html, render_index_html
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.models import Thread
from agent.multi_agent.checkpoint import CheckpointStore
from agent.recording.store import RunStore
from agent.serve.approvals import ApprovalRegistry, map_api_decision
from agent.serve.auth import authorize_request_v2, extract_bearer_token
from agent.serve.dashboard import render_dashboard_html, render_login_html
from agent.serve.turn_runner import TurnRunner
from agent.serve.sessions import SessionStore
from agent.serve.users import merge_rbac_users
from agent.execution.sync.service import resolve_sync_path
from agent.settings import ServeSettings
from agent.store import ThreadStore
from agent.telemetry import trace_span
from agent.metrics import MetricsCollector


class ServeContext:
    def __init__(
        self,
        *,
        store: ThreadStore,
        run_store: RunStore,
        settings: ServeSettings,
        auth_token: str,
        session_store: SessionStore | None = None,
        rbac_users: list | None = None,
    ) -> None:
        self.store = store
        self.run_store = run_store
        self.settings = settings
        self.auth_token = auth_token
        self.session_store = session_store
        self.rbac_users = rbac_users or []


class AgentHttpHandler(BaseHTTPRequestHandler):
    ctx: ServeContext | None = None
    store: ThreadStore | None = None
    run_store: RunStore | None = None
    principal = None

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

    def _dispatch(self, method: str) -> None:
        start = time.monotonic()
        path_clean = unquote(urlparse(self.path).path.rstrip("/")) or "/"
        status_code = 200
        try:
            if method == "GET":
                self.do_GET_inner()
            else:
                self.do_POST_inner()
        except Exception:
            status_code = 500
            MetricsCollector.global_collector().inc_error("serve")
            raise
        finally:
            duration = time.monotonic() - start
            MetricsCollector.global_collector().observe(
                "agent_http_request_duration_seconds", duration, label=path_clean
            )

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_GET_inner(self) -> None:
        if not self._authorize():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        if path == "/login":
            self._html_response(render_login_html())
            return

        if path == "/auth/me":
            p = self.principal
            self._json_response(
                {"name": p.name if p else "anonymous", "role": p.role if p else "admin"}
            )
            return

        if path == "/metrics":
            self._metrics_response()
            return

        if path == "/metrics/prometheus":
            self._prometheus_metrics_response()
            return

        if path == "/":
            ctx = self._get_ctx()
            if ctx.settings.enable_turn_start:
                role = self.principal.role if self.principal else "admin"
                name = self.principal.name if self.principal else "legacy"
                session_mode = ctx.settings.auth_mode in ("session", "both")
                self._html_response(
                    render_dashboard_html(
                        token=ctx.auth_token if ctx.auth_token and not session_mode else "",
                        role=role,
                        user_name=name,
                        session_mode=session_mode,
                    )
                )
                return
            threads = ctx.store.list_threads()
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

        if path.startswith("/threads/") and path.endswith("/workers/graph"):
            thread_id = path[len("/threads/") : -len("/workers/graph")]
            self._worker_graph(thread_id)
            return

        if path.startswith("/threads/") and path.endswith("/events"):
            thread_id = path[len("/threads/") : -len("/events")]
            qs = parse_qs(parsed.query)
            turn_qs = qs.get("turn_id", [None])[0]
            self._sse_thread_events(thread_id, turn_id=turn_qs)
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

    def do_POST_inner(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        if path == "/auth/login":
            self._auth_login()
            return

        if not self._authorize():
            return

        if path.startswith("/threads/") and path.endswith("/cancel"):
            thread_id = path[len("/threads/") : -len("/cancel")]
            self._cancel_thread(thread_id)
            return

        if path.startswith("/threads/") and path.endswith("/run"):
            thread_id = path[len("/threads/") : -len("/run")]
            self._start_turn(thread_id)
            return

        if path.startswith("/approvals/"):
            approval_id = path.split("/approvals/", 1)[1]
            self._resolve_approval(approval_id)
            return

        if path == "/sync/resolve":
            self._sync_resolve()
            return

        self._error(404, "Not found")

    def _auth_login(self) -> None:
        ctx = self._get_ctx()
        if ctx.settings.auth_mode not in ("session", "both"):
            self._error(404, "Session auth disabled")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "Invalid JSON")
            return
        token = (data.get("token") or extract_bearer_token(dict(self.headers)) or "").strip()
        if not token:
            self._error(400, "token required")
            return
        client_key = self.client_address[0] if self.client_address else "unknown"
        session_store = ctx.session_store or SessionStore.global_store()
        rec, err = session_store.login(
            token,
            client_key=client_key,
            rbac_enabled=ctx.settings.rbac.enabled,
            rbac_users=ctx.rbac_users,
            legacy_auth_token=ctx.auth_token,
            default_role=ctx.settings.rbac.default_role,
        )
        if rec is None:
            self._error(401, err or "Invalid token")
            return
        self._json_response(
            {
                "session_id": rec.session_id,
                "expires_at": rec.expires_at,
                "name": rec.principal_name,
                "role": rec.role,
            }
        )

    def _authorize(self) -> bool:
        ctx = self._get_ctx()
        with trace_span(
            "serve.http.request",
            method=getattr(self, "command", "GET"),
            path=self.path.split("?")[0],
        ):
            public = {"/auth/login", "/login"}
            result = authorize_request_v2(
                self.path,
                dict(self.headers),
                auth_token=ctx.auth_token,
                public_paths=public if ctx.settings.auth_mode in ("session", "both") else None,
                auth_mode=ctx.settings.auth_mode,
                rbac_enabled=ctx.settings.rbac.enabled,
                rbac_users=ctx.rbac_users,
                default_role=ctx.settings.rbac.default_role,
                session_store=ctx.session_store,
                method=getattr(self, "command", "GET"),
            )
            self.principal = result.principal
            if not result.authorized:
                status = 403 if result.principal else 401
                self._error(status, result.error or "Unauthorized")
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
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
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
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
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

    def _metrics_response(self) -> None:
        snap = MetricsCollector.global_collector().snapshot()
        self._json_response(
            {
                "counters": snap.counters,
                "labeled_counters": snap.labeled_counters,
                "gauges": snap.gauges,
                "histograms": snap.histograms,
                "labeled_histograms": snap.labeled_histograms,
            }
        )

    def _prometheus_metrics_response(self) -> None:
        body = MetricsCollector.global_collector().to_prometheus().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
    generate_self_signed: bool = False,
) -> None:
    store = ThreadStore()
    run_store = RunStore()
    cfg = settings or ServeSettings(host=host, port=port)
    token = auth_token if auth_token is not None else cfg.auth_token
    rbac_users = merge_rbac_users(cfg.rbac.users)
    session_store = None
    if cfg.auth_mode in ("session", "both"):
        persist = Path.home() / ".agent-cli" / "sessions.json" if cfg.session_persist else None
        session_store = SessionStore.global_store(ttl_sec=cfg.session_ttl_sec, persist_path=persist)

    if not token and not cfg.rbac.enabled and cfg.auth_mode == "bearer":
        token = secrets.token_urlsafe(24)
        print(f"[agent serve] generated auth token: {token}")

    ctx = ServeContext(
        store=store,
        run_store=run_store,
        settings=cfg,
        auth_token=token or "",
        session_store=session_store,
        rbac_users=rbac_users,
    )

    class Handler(AgentHttpHandler):
        pass

    Handler.ctx = ctx
    Handler.store = store
    Handler.run_store = run_store

    bind_host = host or cfg.host
    bind_port = port or cfg.port
    server = ThreadingHTTPServer((bind_host, bind_port), Handler)

    scheme = "http"
    if cfg.tls.enabled or generate_self_signed:
        from agent.serve.tls import expand_path, generate_self_signed_cert, load_ssl_context

        cert_file = expand_path(cfg.tls.cert_file)
        key_file = expand_path(cfg.tls.key_file)
        if generate_self_signed or cfg.tls.auto_generate_self_signed:
            info = generate_self_signed_cert(cert_file, key_file, host=bind_host)
            print(
                f"[agent serve] self-signed cert fingerprint (sha256 prefix): "
                f"{info.get('fingerprint_sha256_prefix')}"
            )
        ssl_ctx = load_ssl_context(cert_file, key_file)
        server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    print(f"agent serve listening on {scheme}://{bind_host}:{bind_port}")
    if cfg.rbac.enabled:
        print(f"  RBAC: enabled ({len(rbac_users)} users, default_role={cfg.rbac.default_role})")
    if cfg.auth_mode in ("session", "both"):
        print("  Auth: session + bearer" if cfg.auth_mode == "both" else "  Auth: session")
    elif token:
        print("  Auth: Bearer token required")
    else:
        print("  Auth: disabled")
    print("  GET /              HTML thread list")
    print("  GET /threads       JSON thread list")
    print("  GET /threads/{id}/events  SSE event stream")
    print("  GET /metrics       JSON metrics")
    print("  GET /metrics/prometheus  Prometheus text metrics")
    if cfg.enable_turn_start:
        print("  POST /threads/{id}/run     Start turn (requires enable_turn_start)")
        print("  POST /approvals/{id}       Approve/deny pending tool")
    print("  POST /threads/{id}/cancel  Cancel active turn")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
