"""Authentication and authorization routes for agent serve."""
from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent.serve.auth import authorize_request_v2, extract_bearer_token, extract_query_token, extract_session_token
from agent.serve.dashboard import render_login_html
from agent.serve.http_response import HttpResponseMixin
from agent.serve.oidc import oidc_mode_active
from agent.serve.policy_gate import enforce_login_policy, enforce_request_policy
from agent.serve.request_limits import handle_body_error, read_limited_body, read_limited_json
from agent.serve.sessions import SessionStore
from agent.auth.webhooks.revoke import revoke_for_event, verify_signature
from agent.auth.webhooks.oidc_events import parse_oidc_event
from agent.metrics import MetricsCollector
from agent.telemetry import trace_span


class AuthRoutesMixin(HttpResponseMixin):
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
        try:
            data = read_limited_json(self, max_bytes=ctx.settings.max_request_body_bytes)
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        if not isinstance(data, dict):
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
        try:
            data = read_limited_json(self, max_bytes=ctx.settings.max_request_body_bytes)
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        if not isinstance(data, dict):
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
        try:
            body = read_limited_body(self, max_bytes=ctx.settings.max_request_body_bytes, default=b"")
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        secret = os.environ.get(wh.shared_secret_env, "")
        sig = self.headers.get("X-Agent-Signature") or self.headers.get("x-agent-signature") or ""
        if not verify_signature(body, sig, secret):
            MetricsCollector.global_collector().inc_labeled("agent_auth_webhook_total", "bad_signature")
            self._error(401, "Invalid signature")
            return
        try:
            payload = __import__("json").loads(body.decode("utf-8") or "{}")
        except (__import__("json").JSONDecodeError, UnicodeDecodeError):
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
            cookie = f"agent_session={rec.session_id}; Path=/; HttpOnly; SameSite=Lax"
            if ctx.settings.tls.enabled:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
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
                allow_query_tokens=ctx.settings.allow_query_tokens,
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
