from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.auth.oidc_tokens import OidcTokenRecord, OidcTokenStore
from agent.metrics import MetricsCollector
from agent.serve.oidc import OidcClient, OidcDeviceFlow, OidcRoleMapping, ServeOidcSettings
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.serve.sessions import SessionStore
from agent.settings import ServeSettings


@pytest.fixture(autouse=True)
def _reset() -> None:
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()
    yield
    SessionStore.reset_for_tests()
    MetricsCollector.reset_for_tests()


def _oidc_settings(**kw) -> ServeOidcSettings:
    defaults = dict(
        enabled=True,
        issuer_url="https://idp.example.com",
        client_id="cid",
        device_code_enabled=True,
        role_mapping=OidcRoleMapping(
            admin_groups=["admins"],
            operator_groups=["ops"],
            default_role="viewer",
        ),
    )
    defaults.update(kw)
    return ServeOidcSettings(**defaults)


def test_start_device_flow_mocked() -> None:
    client = OidcClient(_oidc_settings())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "device_code": "dc",
        "user_code": "ABCD-1234",
        "verification_uri": "https://idp.example.com/device",
        "verification_uri_complete": "https://idp.example.com/device?code=ABCD",
        "expires_in": 600,
        "interval": 5,
    }
    mock_resp.raise_for_status = MagicMock()
    http = MagicMock()
    http.post.return_value = mock_resp
    flow = client.start_device_flow(http_client=http)
    assert flow.user_code == "ABCD-1234"
    assert flow.device_code == "dc"


def test_poll_pending_then_success() -> None:
    client = OidcClient(_oidc_settings())
    flow = OidcDeviceFlow(
        device_code="dc",
        user_code="U",
        verification_uri="https://x",
        verification_uri_complete="https://x?u=U",
        expires_in=600,
        interval=1,
        created_at=time.time(),
        client_id="cid",
    )
    client._device_pending["dc"] = flow
    pending = MagicMock()
    pending.status_code = 400
    pending.json.return_value = {"error": "authorization_pending"}
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {
        "access_token": "at",
        "id_token": _fake_id_token({"sub": "u1", "email": "a@x.com", "groups": ["ops"]}),
    }
    success.raise_for_status = MagicMock()
    http = MagicMock()
    http.post.side_effect = [pending, success]
    status, data = client.poll_device_token("dc", http_client=http)
    assert status == "pending"
    status, data = client.poll_device_token("dc", http_client=http)
    assert status == "success"
    assert data is not None


def test_poll_expired() -> None:
    client = OidcClient(_oidc_settings())
    flow = OidcDeviceFlow(
        device_code="dc",
        user_code="U",
        verification_uri="https://x",
        verification_uri_complete="https://x",
        expires_in=1,
        interval=1,
        created_at=time.time() - 10,
        client_id="cid",
    )
    client._device_pending["dc"] = flow
    status, _ = client.poll_device_token("dc")
    assert status == "expired"


def test_poll_error_response() -> None:
    client = OidcClient(_oidc_settings())
    flow = OidcDeviceFlow(
        device_code="dc",
        user_code="U",
        verification_uri="https://x",
        verification_uri_complete="https://x",
        expires_in=600,
        interval=1,
        created_at=time.time(),
        client_id="cid",
    )
    client._device_pending["dc"] = flow
    resp = MagicMock()
    resp.status_code = 400
    resp.json.return_value = {"error": "access_denied"}
    http = MagicMock()
    http.post.return_value = resp
    status, _ = client.poll_device_token("dc", http_client=http)
    assert status == "error"


def test_role_mapping_on_claims() -> None:
    client = OidcClient(_oidc_settings())
    claims = {"email": "a@x.com", "groups": ["ops"]}
    principal = client.principal_from_claims(claims)
    assert principal.role == "operator"


def test_device_start_endpoint() -> None:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "POST"
    handler.path = "/auth/oidc/device/start"
    handler.headers = {}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.rfile = BytesIO(b"{}")
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    oidc = _oidc_settings()
    client = OidcClient(oidc)
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=ServeSettings(oidc=oidc),
        auth_token="",
        session_store=SessionStore.global_store(),
        oidc_client=client,
    )
    with patch.object(client, "start_device_flow") as mock_start:
        mock_start.return_value = OidcDeviceFlow(
            device_code="dc",
            user_code="CODE",
            verification_uri="https://v",
            verification_uri_complete="https://v?c=CODE",
            expires_in=600,
            interval=5,
            created_at=time.time(),
            client_id="cid",
        )
        handler.do_POST()
    assert handler.send_response.call_args[0][0] == 200


def test_device_poll_success_creates_session() -> None:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "POST"
    handler.path = "/auth/oidc/device/poll"
    handler.headers = {}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    body_bytes = json.dumps({"device_code": "dc"}).encode()
    handler.rfile = BytesIO(body_bytes)
    handler.headers = {"Content-Length": str(len(body_bytes))}
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    oidc = _oidc_settings()
    client = OidcClient(oidc)
    flow = OidcDeviceFlow(
        device_code="dc",
        user_code="U",
        verification_uri="https://x",
        verification_uri_complete="https://x",
        expires_in=600,
        interval=1,
        created_at=time.time(),
        client_id="cid",
    )
    client._device_pending["dc"] = flow
    store = SessionStore.global_store()
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=ServeSettings(oidc=oidc),
        auth_token="",
        session_store=store,
        oidc_client=client,
    )
    token_data = {
        "access_token": "at",
        "id_token": _fake_id_token({"sub": "u1", "email": "ci@example.com", "groups": ["admins"]}),
    }
    with patch.object(client, "poll_device_token", return_value=("success", token_data)):
        handler.do_POST()
    assert handler.send_response.call_args[0][0] == 200
    body = json.loads(handler.wfile.getvalue().decode())
    assert body["role"] == "admin"
    assert store.get_session(body["session_id"]) is not None


def test_device_poll_timeout_metric() -> None:
    client = OidcClient(_oidc_settings())
    flow = OidcDeviceFlow(
        device_code="dc",
        user_code="U",
        verification_uri="https://x",
        verification_uri_complete="https://x",
        expires_in=600,
        interval=1,
        created_at=time.time(),
        client_id="cid",
    )
    client._device_pending["dc"] = flow
    resp = MagicMock()
    resp.status_code = 400
    resp.json.return_value = {"error": "expired_token"}
    http = MagicMock()
    http.post.return_value = resp
    status, _ = client.poll_device_token("dc", http_client=http)
    assert status == "expired"


def test_token_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "oidc.json"
    store = OidcTokenStore(path)
    rec = OidcTokenRecord(
        issuer_url="https://idp",
        client_id="c",
        access_token="at",
        refresh_token="rt",
        email="a@x.com",
        role="operator",
    )
    store.save(rec)
    loaded = store.load()
    assert loaded is not None
    assert loaded.access_token == "at"
    assert loaded.role == "operator"


def test_token_store_clear(tmp_path: Path) -> None:
    path = tmp_path / "oidc.json"
    store = OidcTokenStore(path)
    store.save(OidcTokenRecord(issuer_url="x", client_id="c"))
    store.clear()
    assert store.load() is None


def test_device_disabled_404() -> None:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "POST"
    handler.path = "/auth/oidc/device/start"
    handler.headers = {}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.rfile = BytesIO(b"{}")
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    oidc = _oidc_settings(device_code_enabled=False)
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=ServeSettings(oidc=oidc),
        auth_token="",
        oidc_client=OidcClient(oidc),
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 404


def test_device_metrics_on_success() -> None:
    client = OidcClient(_oidc_settings())
    flow = OidcDeviceFlow(
        device_code="dc",
        user_code="U",
        verification_uri="https://x",
        verification_uri_complete="https://x",
        expires_in=600,
        interval=1,
        created_at=time.time(),
        client_id="cid",
    )
    client._device_pending["dc"] = flow
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"access_token": "a", "id_token": _fake_id_token({"sub": "s"})}
    success.raise_for_status = MagicMock()
    http = MagicMock()
    http.post.return_value = success
    client.poll_device_token("dc", http_client=http)
    assert "agent_auth_device_code_total" in MetricsCollector.global_collector().to_prometheus()


def _fake_id_token(claims: dict) -> str:
    import base64
    import json as _json

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(_json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"
