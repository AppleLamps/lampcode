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
from agent.serve.approvals import ApprovalRegistry, map_api_decision
from agent.serve.auth import authorize_request_v2, extract_bearer_token, extract_query_token, extract_session_token
from agent.serve.dashboard import render_dashboard_html, render_login_html
from agent.serve.ide import (
    IdeError,
    file_diff_from_thread,
    list_tree,
    read_file,
    resolve_thread_cwd,
    write_file_atomic,
)
from agent.serve.turn_runner import TurnRunner
from agent.serve.sessions import SessionStore
from agent.serve.users import merge_rbac_users
from agent.serve.oidc import OidcClient, oidc_mode_active
from agent.serve.policy_gate import enforce_login_policy, enforce_request_policy
from agent.execution.sync.service import resolve_sync_path
from agent.settings import ServeSettings
from agent.store import ThreadStore
from agent.telemetry import trace_span


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
        oidc_client: OidcClient | None = None,
        revocation_registry: SessionRevocationRegistry | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self.store = store
        self.run_store = run_store
        self.settings = settings
        self.auth_token = auth_token
        self.session_store = session_store
        self.rbac_users = rbac_users or []
        self.oidc_client = oidc_client
        self.revocation_registry = revocation_registry or SessionRevocationRegistry()
        self.emitter = emitter or EventEmitter()


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
            if p and self._get_ctx().session_store:
                sid = extract_session_token(dict(self.headers)) or extract_query_token(self.path)
                if sid:
                    rec = self._get_ctx().session_store.get_session(sid)
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

    def _ide_get(self, path: str, parsed) -> None:
        ctx = self._get_ctx()
        ide = ctx.settings.ide
        qs = parse_qs(parsed.query)
        thread_id = (qs.get("thread_id") or [None])[0]
        rel_path = (qs.get("path") or ["."])[0]
        if not thread_id:
            self._ide_metric("tree", "missing_thread")
            self._error(400, "thread_id required")
            return
        try:
            thread, cwd = resolve_thread_cwd(ctx.store, thread_id)
        except IdeError as exc:
            self._ide_metric(path.split("/")[-1], "error")
            self._error(exc.status, str(exc))
            return
        try:
            if path == "/ide/tree":
                data = list_tree(
                    cwd,
                    rel_path,
                    max_depth=ide.max_tree_depth,
                    max_entries=ide.max_tree_entries,
                )
                data["thread_id"] = thread.id
                data["cwd"] = str(cwd)
                self._ide_metric("tree", "ok")
                self._json_response(data)
            elif path == "/ide/file":
                data = read_file(cwd, rel_path, max_bytes=ide.max_file_bytes)
                self._ide_metric("file", "ok")
                self._json_response(data)
            elif path == "/ide/diff":
                data = file_diff_from_thread(thread, rel_path)
                self._ide_metric("diff", "ok")
                self._json_response(data)
            elif path == "/ide/history":
                from agent.serve.ide import compute_diff_gutter, file_history_from_thread

                history = file_history_from_thread(thread, rel_path)
                gutter = {"enabled": False}
                if ide.show_diff_gutter and history:
                    try:
                        current = read_file(cwd, rel_path, max_bytes=ide.max_file_bytes)
                        gutter = compute_diff_gutter(current.get("content", ""), history)
                    except IdeError:
                        gutter = {"enabled": False}
                self._ide_metric("history", "ok")
                self._json_response(
                    {
                        "path": rel_path,
                        "history": history,
                        "gutter": gutter,
                        "show_diff_gutter": ide.show_diff_gutter,
                    }
                )
            elif path == "/ide/tabs/state":
                from agent.metrics import MetricsCollector
                from agent.serve.ide import enforce_tab_limit, tabs_state_snapshot

                raw_tabs = (qs.get("tabs") or [""])[0]
                active = (qs.get("active") or [None])[0]
                tabs = [t for t in raw_tabs.split(",") if t] if raw_tabs else []
                trimmed, was_trimmed = enforce_tab_limit(tabs, max_tabs=ide.max_open_tabs)
                if trimmed:
                    MetricsCollector.global_collector().set_gauge("agent_ide_tabs_open", len(trimmed))
                self._json_response(
                    {
                        **tabs_state_snapshot(trimmed, active),
                        "max_open_tabs": ide.max_open_tabs,
                        "trimmed": was_trimmed,
                    }
                )
            elif path == "/ide/diagnostics":
                from agent.serve.ide import safe_resolve
                from agent.serve.ide_diagnostics import run_diagnostics

                if not ide.diagnostics.enabled:
                    self._json_response({"items": []})
                    return
                try:
                    file_path = safe_resolve(cwd, rel_path)
                except IdeError as exc:
                    self._error(exc.status, str(exc))
                    return
                items = run_diagnostics(file_path, settings=ide.diagnostics)
                self._ide_metric("diagnostics", "ok")
                self._json_response({"items": items, "path": rel_path})
            else:
                self._error(404, "Not found")
        except IdeError as exc:
            self._ide_metric(path.split("/")[-1], "error")
            self._error(exc.status, str(exc))

    def _ide_put_file(self, parsed) -> None:
        ctx = self._get_ctx()
        ide = ctx.settings.ide
        qs = parse_qs(parsed.query)
        thread_id = (qs.get("thread_id") or [None])[0]
        rel_path = (qs.get("path") or [None])[0]
        if not thread_id or not rel_path:
            self._ide_metric("file", "missing_params")
            self._error(400, "thread_id and path required")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(body) if body.startswith("{") else {"content": body}
        except json.JSONDecodeError:
            payload = {"content": body}
        content = payload.get("content", "")
        try:
            _, cwd = resolve_thread_cwd(ctx.store, thread_id)
            result = write_file_atomic(cwd, rel_path, content, max_bytes=ide.max_file_bytes)
            email = None
            if ctx.session_store and self.principal:
                sid = extract_session_token(dict(self.headers))
                if sid:
                    rec = ctx.session_store.get_session(sid)
                    email = rec.email if rec else None
            from agent.events import EventEmitter

            EventEmitter(lambda e: None).ide_file_write(
                thread_id,
                path=rel_path,
                bytes_written=result["bytes"],
                user=self.principal.name if self.principal else "unknown",
                role=self.principal.role if self.principal else "unknown",
                email=email,
            )
            self._ide_metric("file", "write_ok")
            self._json_response(result)
        except IdeError as exc:
            self._ide_metric("file", "error")
            self._error(exc.status, str(exc))

    def _ide_metric(self, route: str, result: str) -> None:
        MetricsCollector.global_collector().inc_labeled(
            "agent_ide_requests_total", f"{route}:{result}"
        )

    def _oidc_device_start(self) -> None:
        ctx = self._get_ctx()
        oidc = ctx.settings.oidc
        if not ctx.oidc_client or not oidc or not oidc.device_code_enabled:
            self._error(404, "Device code flow not enabled")
            return
        try:
            flow = ctx.oidc_client.start_device_flow()
            MetricsCollector.global_collector().inc_labeled("agent_auth_device_code_total", "started")
            self._json_response(
                {
                    "device_code": flow.device_code,
                    "user_code": flow.user_code,
                    "verification_uri": flow.verification_uri,
                    "verification_uri_complete": flow.verification_uri_complete,
                    "expires_in": flow.expires_in,
                    "interval": flow.interval,
                }
            )
        except Exception as exc:
            MetricsCollector.global_collector().inc_labeled("agent_auth_device_code_total", "start_error")
            self._error(502, f"Device flow start failed: {exc}")

    def _oidc_device_poll(self) -> None:
        ctx = self._get_ctx()
        if not ctx.oidc_client or not ctx.session_store:
            self._error(404, "OIDC not configured")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "Invalid JSON")
            return
        device_code = (data.get("device_code") or "").strip()
        if not device_code:
            self._error(400, "device_code required")
            return
        status, token_data = ctx.oidc_client.poll_device_token(device_code)
        if status == "pending":
            MetricsCollector.global_collector().inc_labeled("agent_auth_device_code_total", "pending")
            self._json_response({"status": "pending"})
            return
        if status == "expired":
            MetricsCollector.global_collector().inc_labeled("agent_auth_device_code_total", "expired")
            self._error(408, "Device code expired")
            return
        if status != "success" or not token_data:
            MetricsCollector.global_collector().inc_labeled("agent_auth_device_code_total", "error")
            self._error(401, "Device authorization failed")
            return
        claims = ctx.oidc_client.claims_from_token_response(token_data)
        principal = ctx.oidc_client.principal_from_claims(claims)
        ok, policy_err = enforce_login_policy(
            role=principal.role,
            claims=claims,
            settings=ctx.settings.policy,
            tls_enabled=ctx.settings.tls.enabled,
            emitter=ctx.emitter,
        )
        if not ok:
            self._error(403, policy_err or "Login denied by policy")
            return
        oidc = ctx.settings.oidc
        email_key = oidc.role_mapping.claim_email_key if oidc else "email"
        groups_key = oidc.role_mapping.claim_groups_key if oidc else "groups"
        groups_raw = claims.get(groups_key, [])
        if isinstance(groups_raw, str):
            groups_raw = [groups_raw]
        rec = ctx.session_store.create_session(
            principal,
            ttl_sec=oidc.session_ttl_sec if oidc else None,
            subject=str(claims.get("sub", "")),
            email=str(claims.get(email_key, "")),
            groups=[str(g) for g in groups_raw] if isinstance(groups_raw, list) else [],
            claims=claims,
        )
        MetricsCollector.global_collector().inc_labeled("agent_auth_device_code_total", "success")
        self._json_response(
            {
                "status": "success",
                "session_id": rec.session_id,
                "role": rec.role,
                "email": rec.email,
            }
        )

    def _auth_login(self) -> None:
        ctx = self._get_ctx()
        mode = ctx.settings.auth_mode.lower()
        if not any(x in mode for x in ("session", "both", "bearer", "oidc")):
            self._error(404, "Token auth disabled")
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

    def _auth_logout(self) -> None:
        ctx = self._get_ctx()
        session_id = extract_session_token(dict(self.headers))
        if session_id and ctx.session_store:
            ctx.session_store.revoke_session(session_id)
        self._json_response({"ok": True})

    def _oidc_webhook(self) -> None:
        import os

        ctx = self._get_ctx()
        wh = ctx.settings.webhooks
        if not wh.enabled:
            self._error(404, "Webhooks disabled")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        secret = os.environ.get(wh.shared_secret_env, "")
        sig = self.headers.get("X-Agent-Signature") or self.headers.get("x-agent-signature") or ""
        if not verify_signature(body, sig, secret):
            MetricsCollector.global_collector().inc_labeled("agent_auth_webhook_total", "bad_signature")
            self._error(401, "Invalid signature")
            return
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._error(400, "Invalid JSON")
            return
        event = parse_oidc_event(payload)
        if ctx.emitter:
            ctx.emitter.auth_webhook_received(event=event.get("event", ""), subject=event.get("subject", ""))
        result = revoke_for_event(
            event,
            session_store=ctx.session_store,
            registry=ctx.revocation_registry,
            revoke_on_events=wh.revoke_on_events,
            revoke_all_subject_sessions=wh.revoke_all_subject_sessions,
            emitter=ctx.emitter,
        )
        self._json_response({"ok": True, **result})

    def _oidc_login_redirect(self) -> None:
        ctx = self._get_ctx()
        if not ctx.oidc_client:
            self._error(404, "OIDC not configured")
            return
        url, _ = ctx.oidc_client.start_login()
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _oidc_callback(self) -> None:
        ctx = self._get_ctx()
        if not ctx.oidc_client or not ctx.session_store:
            self._error(404, "OIDC not configured")
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        state = qs.get("state", [None])[0]
        if not code or not state:
            MetricsCollector.global_collector().inc_labeled("agent_auth_oidc_login_total", "missing_params")
            self._error(400, "missing code or state")
            return
        oidc_state = ctx.oidc_client.validate_state(state)
        if oidc_state is None:
            MetricsCollector.global_collector().inc_labeled("agent_auth_oidc_login_total", "invalid_state")
            self._error(400, "invalid state")
            return
        try:
            claims = ctx.oidc_client.exchange_code(code, oidc_state)
            principal = ctx.oidc_client.principal_from_claims(claims)
            ok, policy_err = enforce_login_policy(
                role=principal.role,
                claims=claims,
                settings=ctx.settings.policy,
                tls_enabled=ctx.settings.tls.enabled,
                emitter=ctx.emitter,
            )
            if not ok:
                MetricsCollector.global_collector().inc_labeled("agent_auth_oidc_login_total", "policy_denied")
                self._error(403, policy_err or "Login denied by policy")
                return
            oidc = ctx.settings.oidc
            email_key = oidc.role_mapping.claim_email_key if oidc else "email"
            groups_key = oidc.role_mapping.claim_groups_key if oidc else "groups"
            groups_raw = claims.get(groups_key, [])
            if isinstance(groups_raw, str):
                groups_raw = [groups_raw]
            rec = ctx.session_store.create_session(
                principal,
                ttl_sec=oidc.session_ttl_sec if oidc else None,
                subject=str(claims.get("sub", "")),
                email=str(claims.get(email_key, "")),
                groups=[str(g) for g in groups_raw] if isinstance(groups_raw, list) else [],
                claims=claims,
            )
            MetricsCollector.global_collector().inc_labeled("agent_auth_oidc_login_total", "success")
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"agent_session={rec.session_id}; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
        except Exception:
            MetricsCollector.global_collector().inc_labeled("agent_auth_oidc_login_total", "failure")
            self._error(401, "OIDC login failed")

    def _public_paths(self) -> set[str]:
        paths = {
            "/auth/login",
            "/login",
            "/auth/oidc/login",
            "/auth/oidc/callback",
            "/auth/oidc/device/start",
            "/auth/oidc/device/poll",
        }
        mode = self._get_ctx().settings.auth_mode.lower()
        if any(x in mode for x in ("session", "both", "oidc")):
            return paths
        return set()

    def _authorize(self) -> bool:
        ctx = self._get_ctx()
        with trace_span(
            "serve.http.request",
            method=getattr(self, "command", "GET"),
            path=self.path.split("?")[0],
        ):
            public = self._public_paths()
            result = authorize_request_v2(
                self.path,
                dict(self.headers),
                auth_token=ctx.auth_token,
                public_paths=public if public else None,
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
            if ctx.settings.tls.require_client_cert and not self._client_cert_present():
                self._error(401, "Client certificate required")
                return False
            role = result.principal.role if result.principal else "viewer"
            allowed, policy_err, _decision = enforce_request_policy(
                method=getattr(self, "command", "GET"),
                path=self.path,
                headers=dict(self.headers),
                role=role,
                settings=ctx.settings.policy,
                session_store=ctx.session_store,
                revocation_registry=ctx.revocation_registry,
                tls_enabled=ctx.settings.tls.enabled,
                emitter=ctx.emitter,
            )
            if not allowed:
                status = 401 if policy_err and "revoked" in (policy_err or "").lower() else 403
                self._error(status, policy_err or "Policy denied")
                return False
            return True

    def _client_cert_present(self) -> bool:
        try:
            cert = self.connection.getpeercert()
            return bool(cert)
        except (AttributeError, OSError):
            return False

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
