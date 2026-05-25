from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from agent.auth.policy.sessions import SessionRevocationRegistry
from agent.auth.webhooks.oidc_events import parse_oidc_event
from agent.auth.webhooks.revoke import compute_signature, revoke_for_event, verify_signature
from agent.metrics import MetricsCollector
from agent.serve.rbac import AuthPrincipal
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.serve.sessions import SessionStore
from agent.settings import ServeSettings, ServeWebhookSettings


@pytest.fixture(autouse=True)
def _reset() -> None:
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()
    yield
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()


def test_parse_oidc_event() -> None:
    ev = parse_oidc_event({"event": "role_changed", "subject": "user-123", "groups": ["admins"]})
    assert ev["event"] == "role_changed"
    assert ev["subject"] == "user-123"
    assert ev["groups"] == ["admins"]


def test_parse_oidc_event_sub_alias() -> None:
    ev = parse_oidc_event({"type": "session_revoked", "sub": "u1"})
    assert ev["event"] == "session_revoked"
    assert ev["subject"] == "u1"


def test_compute_and_verify_signature() -> None:
    body = b'{"event":"role_changed"}'
    secret = "test-secret"
    sig = compute_signature(body, secret)
    assert verify_signature(body, sig, secret)


def test_bad_signature_rejected() -> None:
    assert not verify_signature(b"{}", "sha256=deadbeef", "secret")


def test_missing_secret_rejected() -> None:
    assert not verify_signature(b"{}", "sha256=abc", "")


def test_revoke_by_session_id(tmp_path: Path) -> None:
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    store = SessionStore(persist_path=None)
    rec = store.create_session(AuthPrincipal(name="u", role="operator", auth_method="oidc"), subject="sub1")
    result = revoke_for_event(
        {"event": "session_revoked", "session_id": rec.session_id, "subject": "sub1"},
        session_store=store,
        registry=reg,
        revoke_on_events=["session_revoked"],
    )
    assert result["revoked"] == 1
    assert reg.is_revoked(rec.session_id)


def test_revoke_all_subject_sessions(tmp_path: Path) -> None:
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    store = SessionStore(persist_path=None)
    r1 = store.create_session(AuthPrincipal(name="a", role="viewer", auth_method="oidc"), subject="user-123")
    r2 = store.create_session(AuthPrincipal(name="b", role="admin", auth_method="oidc"), subject="user-123")
    result = revoke_for_event(
        {"event": "role_changed", "subject": "user-123"},
        session_store=store,
        registry=reg,
        revoke_on_events=["role_changed"],
        revoke_all_subject_sessions=True,
    )
    assert result["revoked"] == 2
    assert reg.is_revoked(r1.session_id)
    assert reg.is_revoked(r2.session_id)


def test_unknown_event_ignored(tmp_path: Path) -> None:
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    result = revoke_for_event(
        {"event": "ping", "subject": "x"},
        session_store=None,
        registry=reg,
        revoke_on_events=["role_changed"],
    )
    assert result.get("ignored") is True
    assert result["revoked"] == 0


def test_password_changed_event(tmp_path: Path) -> None:
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    store = SessionStore(persist_path=None)
    rec = store.create_session(AuthPrincipal(name="u", role="viewer", auth_method="oidc"), subject="u9")
    revoke_for_event(
        {"event": "password_changed", "subject": "u9"},
        session_store=store,
        registry=reg,
        revoke_on_events=["password_changed"],
        revoke_all_subject_sessions=True,
    )
    assert reg.is_revoked(rec.session_id)


def test_webhook_metrics(tmp_path: Path) -> None:
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    revoke_for_event(
        {"event": "role_changed", "subject": "none"},
        session_store=SessionStore(persist_path=None),
        registry=reg,
        revoke_on_events=["role_changed"],
        revoke_all_subject_sessions=True,
    )
    snap = MetricsCollector.global_collector().snapshot()
    assert "agent_auth_webhook_total" in snap.labeled_counters


def _webhook_handler(tmp_path: Path, secret: str = "hook-secret") -> AgentHttpHandler:
    import os

    os.environ["AGENT_WEBHOOK_SECRET"] = secret
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "POST"
    handler.path = "/auth/webhooks/oidc-events"
    body = json.dumps({"event": "role_changed", "subject": "user-123"}).encode()
    handler.headers = {}
    handler.rfile = BytesIO(body)
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler._json_response = MagicMock(side_effect=lambda b, status=200: handler.wfile.write(json.dumps(b).encode()))
    handler._error = MagicMock()
    store = SessionStore(persist_path=None)
    rec = store.create_session(AuthPrincipal(name="u", role="operator", auth_method="oidc"), subject="user-123")
    settings = ServeSettings(
        webhooks=ServeWebhookSettings(
            enabled=True,
            shared_secret_env="AGENT_WEBHOOK_SECRET",
            revoke_on_events=["role_changed"],
        )
    )
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=settings,
        auth_token="",
        session_store=store,
        revocation_registry=reg,
    )
    handler._stored_session = rec.session_id
    return handler


def test_webhook_endpoint_bad_signature(tmp_path: Path) -> None:
    h = _webhook_handler(tmp_path)
    body = h.rfile.getvalue()
    h.headers = {"Content-Length": str(len(body))}
    h.do_POST_inner()
    h._error.assert_called()


def test_webhook_endpoint_valid_signature(tmp_path: Path) -> None:
    h = _webhook_handler(tmp_path)
    body = h.rfile.getvalue()
    sig = compute_signature(body, "hook-secret")
    h.headers = {"X-Agent-Signature": sig, "Content-Length": str(len(body))}
    h.rfile = BytesIO(body)
    h.do_POST_inner()
    h._json_response.assert_called()


def test_webhook_disabled_404() -> None:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.path = "/auth/webhooks/oidc-events"
    handler.headers = {}
    handler.rfile = BytesIO(b"{}")
    handler._error = MagicMock()
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=ServeSettings(webhooks=ServeWebhookSettings(enabled=False)),
        auth_token="",
    )
    handler._oidc_webhook()
    handler._error.assert_called_with(404, "Webhooks disabled")


def test_sessions_for_subject() -> None:
    store = SessionStore(persist_path=None)
    store.create_session(AuthPrincipal(name="a", role="viewer", auth_method="oidc"), subject="same")
    store.create_session(AuthPrincipal(name="b", role="admin", auth_method="oidc"), subject="other")
    assert len(store.sessions_for_subject("same")) == 1


def test_revoke_session_id_only_no_subject(tmp_path: Path) -> None:
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    result = revoke_for_event(
        {"event": "session_revoked", "session_id": "sess-abc"},
        session_store=None,
        registry=reg,
        revoke_on_events=["session_revoked"],
    )
    assert result["revoked"] == 1


def test_signature_header_without_prefix() -> None:
    body = b"x"
    secret = "s"
    digest = compute_signature(body, secret).split("=", 1)[1]
    assert verify_signature(body, digest, secret)


def test_groups_string_parsed() -> None:
    ev = parse_oidc_event({"event": "role_changed", "subject": "u", "groups": "admin"})
    assert ev["groups"] == ["admin"]
