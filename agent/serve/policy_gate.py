from __future__ import annotations

import time
from typing import Any

from agent.auth.policy.engine import (
    CONTROL_ACTIONS,
    PolicyContext,
    action_for_route,
    evaluate,
    evaluate_login,
)
from agent.auth.policy.introspection import introspect_token
from agent.auth.policy.rules import ServePolicySettings
from agent.auth.policy.sessions import SessionRevocationRegistry
from agent.metrics import MetricsCollector
from agent.serve.auth import extract_bearer_token, extract_session_token
from agent.serve.sessions import SessionStore


def request_is_https(headers: dict[str, str], *, tls_enabled: bool = False) -> bool:
    proto = (headers.get("X-Forwarded-Proto") or headers.get("x-forwarded-proto") or "").lower()
    if proto == "https":
        return True
    if tls_enabled:
        return True
    return False


def check_bearer_introspection(
    token: str,
    settings: ServePolicySettings,
    *,
    http_post: Any = None,
) -> tuple[bool, str]:
    if not settings.enabled or not settings.introspection_url:
        return True, ""
    result = introspect_token(
        token,
        url=settings.introspection_url,
        client_id=settings.introspection_client_id,
        client_secret=settings.introspection_client_secret,
        http_post=http_post,
    )
    if result.get("skipped"):
        return True, ""
    if not result.get("active"):
        return False, result.get("error") or "Token inactive"
    return True, ""


def enforce_request_policy(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    role: str,
    settings: ServePolicySettings,
    session_store: SessionStore | None = None,
    revocation_registry: SessionRevocationRegistry | None = None,
    tls_enabled: bool = False,
    emitter: Any = None,
) -> tuple[bool, str | None, str]:
    """Return (allowed, error_message, decision)."""
    if not settings.enabled:
        return True, None, "allow"

    session_id = extract_session_token(headers)
    if session_id and revocation_registry and revocation_registry.is_revoked(session_id):
        MetricsCollector.global_collector().inc("agent_auth_session_revoked_total")
        if emitter:
            emitter.auth_session_revoked(session_id=session_id[:8] + "...", reason="registry")
        return False, "Session revoked", "deny"

    bearer = extract_bearer_token(headers)
    if bearer and settings.introspection_url:
        ok, msg = check_bearer_introspection(bearer, settings)
        if not ok:
            if emitter:
                emitter.auth_policy_denied(role=role, action="bearer.introspect", rule="introspection", message=msg)
            return False, msg or "Token inactive", "deny"

    claims: dict[str, Any] = {}
    session_age = 0.0
    idle_sec = 0.0
    if session_id and session_store:
        rec = session_store.get_session(session_id)
        if rec:
            claims = dict(rec.claims or {})
            now = time.time()
            session_age = now - rec.created_at
            idle_sec = now - (rec.last_activity_at or rec.created_at)
            session_store.touch_session(session_id)

    action = action_for_route(method, path)
    result = evaluate(
        PolicyContext(
            role=role,
            action=action,
            claims=claims,
            https=request_is_https(headers, tls_enabled=tls_enabled),
            session_id=session_id,
            session_age_sec=session_age,
            idle_sec=idle_sec,
        ),
        settings,
    )
    if result.decision == "deny":
        if emitter:
            emitter.auth_policy_denied(
                role=role, action=action, rule=result.rule, message=result.message
            )
        return False, result.message or "Policy denied", "deny"
    if result.decision == "step_up":
        if emitter:
            emitter.auth_policy_denied(
                role=role, action=action, rule=result.rule or "step_up", message=result.message
            )
        return False, result.message or "Step-up authentication required", "step_up"
    return True, None, "allow"


def enforce_login_policy(
    *,
    role: str,
    claims: dict[str, Any],
    settings: ServePolicySettings,
    tls_enabled: bool = False,
    emitter: Any = None,
) -> tuple[bool, str]:
    if not settings.enabled:
        return True, ""
    result = evaluate_login(role=role, claims=claims, settings=settings, https=tls_enabled)
    if result.decision != "allow":
        if emitter:
            emitter.auth_policy_denied(
                role=role, action="auth.login", rule=result.rule, message=result.message
            )
        return False, result.message or "Login denied by policy"
    return True, ""
