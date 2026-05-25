from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.auth.policy.engine import (
    CONTROL_ACTIONS,
    PolicyContext,
    action_for_route,
    evaluate,
    evaluate_login,
)
from agent.auth.policy.introspection import introspect_token
from agent.auth.policy.rules import PolicyRule, ServePolicySettings, parse_policy_rules
from agent.auth.policy.sessions import SessionRevocationRegistry
from agent.events import EventEmitter
from agent.metrics import MetricsCollector
from agent.serve.policy_gate import (
    check_bearer_introspection,
    enforce_login_policy,
    enforce_request_policy,
    request_is_https,
)
from agent.serve.rbac import AuthPrincipal, hash_token
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.serve.sessions import SessionStore
from agent.settings import RbacUser, ServePolicySettings as SettingsPolicy, ServeRbacSettings, ServeSettings


@pytest.fixture(autouse=True)
def _reset() -> None:
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()
    yield
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()


def _policy(**kwargs) -> ServePolicySettings:
    return ServePolicySettings(enabled=True, **kwargs)


def test_policy_disabled_allows() -> None:
    r = evaluate(PolicyContext(role="viewer", action="thread.run"), ServePolicySettings(enabled=False))
    assert r.decision == "allow"


def test_require_https_denies() -> None:
    r = evaluate(PolicyContext(role="admin", action="thread.read", https=False), _policy(require_https=True))
    assert r.decision == "deny"


def test_max_session_age_denies() -> None:
    r = evaluate(
        PolicyContext(role="admin", action="thread.read", session_age_sec=30000),
        _policy(max_session_age_sec=28800),
    )
    assert r.decision == "deny"


def test_idle_timeout_denies() -> None:
    r = evaluate(
        PolicyContext(role="admin", action="thread.read", idle_sec=4000),
        _policy(idle_timeout_sec=3600),
    )
    assert r.decision == "deny"


def test_deny_action_rule() -> None:
    rules = [
        PolicyRule(
            name="block-viewer-run",
            match_roles=["viewer"],
            deny_actions=["thread.run"],
            deny_message="Viewers cannot run",
        )
    ]
    r = evaluate(PolicyContext(role="viewer", action="thread.run"), _policy(rules=rules))
    assert r.decision == "deny"
    assert "Viewer" in r.message


def test_mfa_claim_required() -> None:
    rules = [
        PolicyRule(
            name="require-mfa",
            match_roles=["operator", "admin"],
            require_claims={"amr": "mfa"},
            deny_message="MFA required",
        )
    ]
    r = evaluate(
        PolicyContext(role="operator", action="thread.run", claims={}),
        _policy(rules=rules, step_up_for_control_actions=False),
    )
    assert r.decision == "deny"
    assert r.message == "MFA required"


def test_mfa_claim_passes() -> None:
    rules = [
        PolicyRule(
            name="require-mfa",
            match_roles=["operator"],
            require_claims={"amr": "mfa"},
        )
    ]
    r = evaluate(
        PolicyContext(role="operator", action="thread.run", claims={"amr": ["mfa"]}),
        _policy(rules=rules),
    )
    assert r.decision == "allow"


def test_step_up_for_control_without_mfa() -> None:
    rules = [
        PolicyRule(
            name="require-mfa",
            match_roles=["operator"],
            require_claims={"amr": "mfa"},
        )
    ]
    r = evaluate(
        PolicyContext(role="operator", action="thread.run", claims={}),
        _policy(rules=rules, step_up_for_control_actions=True),
    )
    assert r.decision in ("deny", "step_up")


def test_action_for_route_run() -> None:
    assert action_for_route("POST", "/threads/abc/run") == "thread.run"


def test_action_for_route_ide_write() -> None:
    assert action_for_route("PUT", "/ide/file") == "ide.write"


def test_control_actions_set() -> None:
    assert "thread.run" in CONTROL_ACTIONS


def test_parse_policy_rules() -> None:
    rules = parse_policy_rules(
        [{"name": "r1", "match_roles": ["admin"], "deny_actions": ["thread.run"]}]
    )
    assert rules[0].name == "r1"


def test_login_policy_denied() -> None:
    rules = [PolicyRule(name="mfa", match_roles=["operator"], require_claims={"amr": "mfa"})]
    ok, msg = enforce_login_policy(
        role="operator",
        claims={},
        settings=_policy(rules=rules),
        tls_enabled=True,
    )
    assert not ok


def test_login_policy_allowed() -> None:
    ok, _ = enforce_login_policy(
        role="operator",
        claims={"amr": ["otp"]},
        settings=ServePolicySettings(enabled=False),
    )
    assert ok


def test_introspection_inactive_token() -> None:
    def fake_post(url, *, data, timeout):
        return {"active": False}

    r = introspect_token("tok", url="https://idp/introspect", http_post=fake_post)
    assert r["active"] is False


def test_introspection_skipped_without_url() -> None:
    r = introspect_token("tok", url="")
    assert r.get("skipped") is True


def test_bearer_introspection_blocks() -> None:
    ok, msg = check_bearer_introspection(
        "bad",
        _policy(introspection_url="https://idp/introspect"),
        http_post=lambda *a, **k: {"active": False},
    )
    assert not ok


def test_revocation_registry() -> None:
    reg = SessionRevocationRegistry(path=Path("/tmp/nonexistent-test-revoke.json"))
    reg.path = Path(__file__).parent / "_tmp_revoke.json"
    if reg.path.is_file():
        reg.path.unlink()
    reg2 = SessionRevocationRegistry(path=reg.path)
    reg2.revoke("session-abc", reason="test")
    assert reg2.is_revoked("session-abc")
    reg2.clear()


def test_revoke_all_except() -> None:
    reg = SessionRevocationRegistry(path=Path(__file__).parent / "_tmp_revoke_all.json")
    if reg.path.is_file():
        reg.path.unlink()
    reg = SessionRevocationRegistry(path=reg.path)
    n = reg.revoke_all_except("keep", ["a", "b", "keep"], reason="bulk")
    assert n == 2
    assert reg.is_revoked("a")
    assert not reg.is_revoked("keep")
    reg.clear()


def test_enforce_request_revoked_session(tmp_path: Path) -> None:
    store = SessionStore(persist_path=None)
    rec = store.create_session(AuthPrincipal(name="u", role="operator", auth_method="session"))
    reg = SessionRevocationRegistry(path=tmp_path / "revoked.json")
    reg.revoke(rec.session_id)
    allowed, err, _ = enforce_request_policy(
        method="GET",
        path="/threads",
        headers={"Cookie": f"agent_session={rec.session_id}"},
        role="operator",
        settings=_policy(),
        session_store=store,
        revocation_registry=reg,
    )
    assert not allowed
    assert "revoked" in (err or "").lower()


def test_request_is_https_forwarded() -> None:
    assert request_is_https({"X-Forwarded-Proto": "https"})


def test_evaluate_login_wrapper() -> None:
    r = evaluate_login(role="admin", claims={}, settings=ServePolicySettings(enabled=False))
    assert r.decision == "allow"


def test_policy_metrics_on_deny() -> None:
    evaluate(PolicyContext(role="viewer", action="thread.run", https=False), _policy())
    snap = MetricsCollector.global_collector().snapshot()
    assert snap.labeled_counters.get("agent_auth_policy_total", {}).get("deny", 0) >= 1


def test_serve_revoked_blocks_threads(tmp_path: Path) -> None:
    store = SessionStore(persist_path=None)
    rec = store.create_session(AuthPrincipal(name="u", role="viewer", auth_method="session"))
    reg = SessionRevocationRegistry(path=tmp_path / "rev.json")
    reg.revoke(rec.session_id)
    settings = ServeSettings(
        rbac=ServeRbacSettings(enabled=True),
        policy=SettingsPolicy(enabled=True),
    )
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "GET"
    handler.path = "/threads"
    handler.headers = {"Cookie": f"agent_session={rec.session_id}"}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=settings,
        auth_token="",
        session_store=store,
        revocation_registry=reg,
    )
    handler._error = MagicMock()
    ok = handler._authorize()
    assert ok is False
    handler._error.assert_called()
