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
from agent.auth.policy.sessions import SessionRevocationRegistry
from agent.auth.webhooks.revoke import revoke_for_event, verify_signature
from agent.auth.webhooks.oidc_events import parse_oidc_event
from agent.events import EventEmitter
from agent.serve.auth import extract_query_token, extract_session_token
from agent.serve.context import ServeContext
from agent.serve.dashboard import render_dashboard_html, render_login_html
from agent.serve.http_response import HttpResponseMixin, thread_to_dict, thread_to_redacted_dict, wants_full_thread_response

_thread_to_dict = thread_to_dict  # compat for tests
from agent.serve.routes import (
    AdminRoutesMixin,
    AuthRoutesMixin,
    IdeRoutesMixin,
    ThreadRoutesMixin,
)
from agent.serve.sessions import SessionStore
from agent.serve.users import merge_rbac_users
from agent.serve.oidc import OidcClient
from agent.settings import ServeSettings
from agent.store import ThreadStore


class AgentHttpHandler(
    AuthRoutesMixin,
    IdeRoutesMixin,
    ThreadRoutesMixin,
    AdminRoutesMixin,
    HttpResponseMixin,
    BaseHTTPRequestHandler,
):
    ctx: ServeContext | None = None
    store: ThreadStore | None = None
    run_store: RunStore | None = None
    principal = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _dispatch(self, method: str) -> None:
        start = time.monotonic()
        path_clean = unquote(urlparse(self.path).path.rstrip("/")) or "/"
        status_code = 200
        try:
            if method == "GET":
                self.do_GET_inner()
            elif method == "PUT":
                self.do_PUT_inner()
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

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._apply_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Agent-Signature")
        self.end_headers()

    def do_GET_inner(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        if path == "/auth/oidc/login":
            self._oidc_login_redirect()
            return

        if path == "/auth/oidc/callback":
            self._oidc_callback()
            return

        if path == "/login":
            ctx = self._get_ctx()
            self._html_response(
                render_login_html(oidc_enabled=ctx.oidc_client is not None)
            )
            return

        if not self._authorize():
            return

        if path == "/auth/me":
            p = self.principal
            rec = None
            ctx = self._get_ctx()
            if p and ctx.session_store:
                sid = extract_session_token(dict(self.headers))
                if not sid and ctx.settings.allow_query_tokens:
                    sid = extract_query_token(self.path)
                if sid:
                    rec = ctx.session_store.get_session(sid)
            self._json_response(
                {
                    "name": p.name if p else "anonymous",
                    "role": p.role if p else "admin",
                    "email": rec.email if rec else None,
                    "auth_method": rec.auth_method if rec else None,
                }
            )
            return

        if path == "/metrics":
            self._metrics_response()
            return

        if path == "/metrics/prometheus":
            self._prometheus_metrics_response()
            return

        if path == "/serve/policy/status":
            ctx = self._get_ctx()
            pol = ctx.settings.policy
            self._json_response(
                {
                    "enabled": pol.enabled,
                    "require_https": pol.require_https,
                    "introspection_configured": bool(pol.introspection_url),
                    "rules": len(pol.rules),
                    "revoked_sessions": len(ctx.revocation_registry.list_revoked()),
                    "step_up_for_control_actions": pol.step_up_for_control_actions,
                }
            )
            return

        if path == "/serve/webhooks/status":
            ctx = self._get_ctx()
            wh = ctx.settings.webhooks
            self._json_response(
                {
                    "enabled": wh.enabled,
                    "path": wh.path,
                    "revoke_on_events": wh.revoke_on_events,
                    "secret_configured": bool(__import__("os").environ.get(wh.shared_secret_env)),
                }
            )
            return

        ctx = self._get_ctx()
        if ctx.settings.ide.enabled and path.startswith("/ide/"):
            self._ide_get(path, parsed)
            return

        if path == "/":
            if ctx.settings.enable_turn_start:
                role = self.principal.role if self.principal else "admin"
                name = self.principal.name if self.principal else "legacy"
                session_mode = "session" in ctx.settings.auth_mode or "oidc" in ctx.settings.auth_mode
                display_name = name
                if self.principal and ctx.session_store:
                    sid = extract_session_token(dict(self.headers))
                    if sid:
                        rec = ctx.session_store.get_session(sid)
                        if rec and rec.email:
                            display_name = f"{rec.email} ({rec.role})"
                self._html_response(
                    render_dashboard_html(
                        token=ctx.auth_token if ctx.auth_token and not session_mode else "",
                        role=role,
                        user_name=display_name,
                        session_mode=session_mode,
                        oidc_enabled=ctx.oidc_client is not None,
                        ide_enabled=ctx.settings.ide.enabled,
                        monaco_cdn=ctx.settings.ide.monaco_cdn,
                        max_open_tabs=ctx.settings.ide.max_open_tabs,
                    )
                )
                return
            threads = ctx.store.list_thread_meta()
            active = sum(
                1 for t in threads if ActiveTurnRegistry.global_registry().is_active(t.id)
            )
            html = render_index_html(threads, active_turns=active)
            self._html_response(html)
            return

        if path == "/threads":
            threads = self._get_ctx().store.list_thread_meta()
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
            if ctx.settings.redact_thread_responses and not wants_full_thread_response(self):
                self._json_response(thread_to_redacted_dict(thread))
            else:
                self._json_response(thread_to_dict(thread))
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

        if path == "/auth/logout":
            self._auth_logout()
            return

        if path == "/auth/oidc/device/start":
            self._oidc_device_start()
            return

        if path == "/auth/oidc/device/poll":
            self._oidc_device_poll()
            return

        ctx = self._get_ctx()
        webhook_path = (ctx.settings.webhooks.path or "/auth/webhooks/oidc-events").rstrip("/") or "/auth/webhooks/oidc-events"
        if ctx.settings.webhooks.enabled and path == webhook_path:
            self._oidc_webhook()
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

    def do_PUT_inner(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"
        if not self._authorize():
            return
        ctx = self._get_ctx()
        if ctx.settings.ide.enabled and path.startswith("/ide/file"):
            self._ide_put_file(parsed)
            return
        self._error(404, "Not found")


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
    oidc_client = None
    mode_lower = cfg.auth_mode.lower()
    if cfg.oidc and cfg.oidc.enabled:
        oidc_client = OidcClient(cfg.oidc)
    if any(x in mode_lower for x in ("session", "both", "oidc")):
        ttl = cfg.oidc.session_ttl_sec if cfg.oidc else cfg.session_ttl_sec
        persist = Path.home() / ".agent-cli" / "sessions.json" if cfg.session_persist else None
        session_store = SessionStore.global_store(ttl_sec=ttl, persist_path=persist)

    if not token and not cfg.rbac.enabled and cfg.auth_mode == "bearer" and not oidc_client:
        token = secrets.token_urlsafe(24)
        print(f"[agent serve] generated auth token: {token}")

    ctx = ServeContext(
        store=store,
        run_store=run_store,
        settings=cfg,
        auth_token=token or "",
        session_store=session_store,
        rbac_users=rbac_users,
        oidc_client=oidc_client,
        revocation_registry=SessionRevocationRegistry(),
        emitter=EventEmitter(),
    )

    class Handler(AgentHttpHandler):
        pass

    Handler.ctx = ctx
    Handler.store = store
    Handler.run_store = run_store

    bind_host = host or cfg.host
    bind_port = port or cfg.port
    remote_bind = bind_host not in ("127.0.0.1", "localhost", "::1")
    if remote_bind and not cfg.allow_remote_bind:
        raise ValueError(
            "Refusing to bind agent serve to a non-local address without serve.allow_remote_bind=true"
        )
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
        ssl_ctx = load_ssl_context(
            cert_file,
            key_file,
            require_client_cert=cfg.tls.require_client_cert,
            client_ca_file=expand_path(cfg.tls.client_ca_file) if cfg.tls.require_client_cert else None,
        )
        server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    print(f"agent serve listening on {scheme}://{bind_host}:{bind_port}")
    if cfg.rbac.enabled:
        print(f"  RBAC: enabled ({len(rbac_users)} users, default_role={cfg.rbac.default_role})")
    if oidc_client:
        print("  Auth: OIDC SSO enabled")
    if any(x in cfg.auth_mode for x in ("session", "both", "oidc")):
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
