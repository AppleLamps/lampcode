from __future__ import annotations

import hashlib
import hmac
from typing import Any

from agent.auth.policy.sessions import SessionRevocationRegistry
from agent.metrics import MetricsCollector
from agent.serve.sessions import SessionStore


def compute_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, header: str, secret: str) -> bool:
    if not secret or not header:
        return False
    expected = compute_signature(body, secret)
    provided = header.strip()
    if provided.startswith("sha256="):
        return hmac.compare_digest(provided, expected)
    return hmac.compare_digest(provided, expected.split("=", 1)[-1])


def revoke_for_event(
    event: dict[str, Any],
    *,
    session_store: SessionStore | None,
    registry: SessionRevocationRegistry,
    revoke_on_events: list[str],
    revoke_all_subject_sessions: bool = True,
    emitter: Any = None,
) -> dict[str, Any]:
    name = event.get("event", "")
    if name not in revoke_on_events:
        MetricsCollector.global_collector().inc_labeled("agent_auth_webhook_total", f"{name}:ignored")
        return {"revoked": 0, "ignored": True, "event": name}

    revoked = 0
    session_id = event.get("session_id")
    subject = event.get("subject", "")

    if session_id:
        registry.revoke(session_id, reason=f"webhook:{name}")
        if session_store:
            session_store.revoke_session(session_id)
        revoked += 1
    elif revoke_all_subject_sessions and subject and session_store:
        for rec in session_store.sessions_for_subject(subject):
            registry.revoke(rec.session_id, reason=f"webhook:{name}")
            session_store.revoke_session(rec.session_id)
            revoked += 1

    if revoked:
        MetricsCollector.global_collector().inc("agent_auth_session_revoked_total", revoked)
        if emitter:
            emitter.auth_session_revoked_bulk(event=name, subject=subject, count=revoked)

    MetricsCollector.global_collector().inc_labeled(
        "agent_auth_webhook_total", f"{name}:{'ok' if revoked else 'noop'}"
    )
    return {"revoked": revoked, "event": name, "subject": subject}
