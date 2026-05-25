from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from agent.metrics import MetricsCollector
from agent.serve.auth import authorize_request_v2
from agent.serve.oidc import OidcClient, OidcRoleMapping, ServeOidcSettings
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.serve.sessions import SessionStore
from agent.settings import RbacUser, ServeRbacSettings, ServeSettings, ServeTlsSettings
from agent.store import ThreadStore
from agent.serve.rbac import hash_token


@pytest.fixture(autouse=True)
def _reset() -> None:
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()
    yield
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()


def _oidc_settings() -> ServeOidcSettings:
    return ServeOidcSettings(
        enabled=True,
        issuer_url="https://login.example.com/tenant/v2.0",
        client_id="client-id",
        redirect_uri="https://127.0.0.1:8765/auth/oidc/callback",
        pkce=True,
        role_mapping=OidcRoleMapping(
            admin_groups=["agent-admins"],
            operator_groups=["agent-operators"],
            default_role="viewer",
            claim_groups_key="groups",
            claim_email_key="email",
        ),
    )


def _handler(**kwargs) -> AgentHttpHandler:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = kwargs.get("command", "GET")
    handler.path = kwargs.get("path", "/")
    handler.headers = kwargs.get("headers", {})
    handler.client_address = ("127.0.0.1", 12345)
    handler.wfile = BytesIO()
    handler.rfile = BytesIO(kwargs.get("body", b""))
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    oidc = kwargs.get("oidc_client", OidcClient(_oidc_settings()))
    settings = kwargs.get(
        "settings",
        ServeSettings(
            enable_control=True,
            enable_turn_start=True,
            auth_mode="oidc+bearer",
            tls=ServeTlsSettings(enabled=True),
            rbac=ServeRbacSettings(enabled=True),
            oidc=_oidc_settings(),
        ),
    )
    handler.ctx = kwargs.get(
        "ctx",
        ServeContext(
            store=ThreadStore(),
            run_store=MagicMock(),
            settings=settings,
            auth_token="legacy-admin",
            session_store=SessionStore.global_store(),
            rbac_users=kwargs.get(
                "rbac_users",
                [RbacUser(name="bob", token_hash=hash_token("bob-secret"), role="viewer")],
            ),
            oidc_client=oidc,
        ),
    )
    return handler


def test_pkce_challenge_generation() -> None:
    client = OidcClient(_oidc_settings())
    url, state = client.start_login()
    assert "code_challenge=" in url
    assert state.code_verifier
    assert state.state in url


def test_state_validation_rejects_unknown() -> None:
    client = OidcClient(_oidc_settings())
    assert client.validate_state("bogus") is None


def test_state_validation_one_time_use() -> None:
    client = OidcClient(_oidc_settings())
    _, state = client.start_login()
    assert client.validate_state(state.state) is not None
    assert client.validate_state(state.state) is None


def test_role_mapping_admin() -> None:
    client = OidcClient(_oidc_settings())
    role = client.map_role({"groups": ["agent-admins"]})
    assert role == "admin"


def test_role_mapping_operator() -> None:
    client = OidcClient(_oidc_settings())
    role = client.map_role({"groups": ["agent-operators"]})
    assert role == "operator"


def test_role_mapping_default_viewer() -> None:
    client = OidcClient(_oidc_settings())
    assert client.map_role({"groups": ["other"]}) == "viewer"


def test_exchange_code_nonce_check() -> None:
    client = OidcClient(_oidc_settings())
    _, state = client.start_login()
    import base64

    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"nonce": "wrong", "sub": "u1", "groups": []}).encode()
    ).decode().rstrip("=")
    fake_token = f"{header}.{payload}.sig"
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id_token": fake_token}
    mock_resp.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp
    with pytest.raises(ValueError, match="nonce"):
        client.exchange_code("code123", state, http_client=mock_http)


def test_oidc_callback_success() -> None:
    handler = _handler(path="/auth/oidc/callback?code=abc&state=s1")
    client = handler.ctx.oidc_client
    _, state = client.start_login()
    handler.path = f"/auth/oidc/callback?code=abc&state={state.state}"
    claims = {"sub": "u1", "email": "a@example.com", "groups": ["agent-operators"], "nonce": state.nonce}
    with patch.object(client, "exchange_code", return_value=claims):
        handler.do_GET()
    assert handler.send_response.call_args[0][0] == 302
    assert any("agent_session=" in str(c) for c in handler.send_header.call_args_list)


def test_oidc_callback_invalid_state() -> None:
    handler = _handler(path="/auth/oidc/callback?code=abc&state=bad")
    handler.do_GET()
    assert handler.send_response.call_args[0][0] == 400


def test_oidc_login_redirect() -> None:
    handler = _handler(path="/auth/oidc/login")
    handler.do_GET()
    assert handler.send_response.call_args[0][0] == 302


def test_session_from_oidc_can_access_threads() -> None:
    store = SessionStore.global_store()
    from agent.serve.rbac import AuthPrincipal

    rec = store.create_session(
        AuthPrincipal(name="a@example.com", role="operator", auth_method="oidc"),
        email="a@example.com",
        groups=["agent-operators"],
    )
    result = authorize_request_v2(
        "/threads",
        {"Authorization": f"Session {rec.session_id}"},
        auth_token="",
        auth_mode="oidc+bearer",
        rbac_enabled=True,
        rbac_users=[],
        session_store=store,
        method="GET",
    )
    assert result.authorized is True
    assert result.principal.role == "operator"


def test_viewer_oidc_forbidden_run() -> None:
    store = SessionStore.global_store()
    from agent.serve.rbac import AuthPrincipal

    rec = store.create_session(
        AuthPrincipal(name="v@example.com", role="viewer", auth_method="oidc"),
    )
    result = authorize_request_v2(
        "/threads/x/run",
        {"Authorization": f"Session {rec.session_id}"},
        auth_token="",
        auth_mode="oidc",
        rbac_enabled=True,
        rbac_users=[],
        session_store=store,
        method="POST",
    )
    assert result.authorized is False


def test_break_glass_bearer_with_oidc_mode() -> None:
    users = [RbacUser(name="alice", token_hash=hash_token("alice-secret"), role="admin")]
    result = authorize_request_v2(
        "/threads",
        {"Authorization": "Bearer alice-secret"},
        auth_token="legacy-admin",
        auth_mode="oidc+bearer",
        rbac_enabled=True,
        rbac_users=users,
        session_store=SessionStore.global_store(),
        method="GET",
    )
    assert result.authorized is True


def test_oidc_login_metrics_success() -> None:
    handler = _handler(path="/auth/oidc/callback?code=abc&state=s1")
    client = handler.ctx.oidc_client
    _, state = client.start_login()
    handler.path = f"/auth/oidc/callback?code=abc&state={state.state}"
    claims = {"sub": "u1", "email": "a@example.com", "groups": [], "nonce": state.nonce}
    with patch.object(client, "exchange_code", return_value=claims):
        handler.do_GET()
    body = MetricsCollector.global_collector().to_prometheus()
    assert "agent_auth_oidc_login_total" in body


def test_login_html_includes_sso() -> None:
    from agent.serve.dashboard import render_login_html

    html = render_login_html(oidc_enabled=True)
    assert "Sign in with SSO" in html
    assert "/auth/oidc/login" in html


def test_principal_from_claims() -> None:
    client = OidcClient(_oidc_settings())
    p = client.principal_from_claims({"sub": "1", "email": "u@test.com", "groups": ["agent-admins"]})
    assert p.role == "admin"
    assert p.auth_method == "oidc"


def test_logout_revokes_session() -> None:
    store = SessionStore.global_store()
    from agent.serve.rbac import AuthPrincipal

    rec = store.create_session(AuthPrincipal(name="x", role="admin", auth_method="oidc"))
    handler = _handler(
        command="POST",
        path="/auth/logout",
        headers={"Authorization": f"Session {rec.session_id}"},
    )
    handler.do_POST()
    assert store.get_session(rec.session_id) is None


def test_decode_jwt_payload() -> None:
    from agent.serve.oidc import _decode_jwt_payload
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"sub": "abc"}).encode()).decode().rstrip("=")
    token = f"hdr.{payload}.sig"
    assert _decode_jwt_payload(token)["sub"] == "abc"


def test_oidc_mode_active_helper() -> None:
    from agent.serve.oidc import oidc_mode_active

    assert oidc_mode_active("oidc+bearer")
    assert not oidc_mode_active("bearer")


def test_public_oidc_routes_no_auth() -> None:
    handler = _handler(path="/auth/oidc/login", headers={})
    handler.do_GET()
    assert handler.send_response.call_args[0][0] == 302
