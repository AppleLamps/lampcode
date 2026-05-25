from __future__ import annotations

import json
import ssl
import threading
from http.client import HTTPSConnection
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.metrics import MetricsCollector
from agent.serve.approvals import ApprovalRegistry
from agent.serve.auth import authorize_request_v2
from agent.serve.rbac import Permission, hash_token, resolve_principal_from_token
from agent.serve.server import AgentHttpHandler, ServeContext, serve
from agent.serve.sessions import SessionStore
from agent.settings import RbacUser, ServeRbacSettings, ServeSettings
from agent.store import ThreadStore


@pytest.fixture(autouse=True)
def _reset() -> None:
    SessionStore.reset_for_tests()
    ApprovalRegistry.reset_for_tests()
    MetricsCollector.reset_for_tests()
    yield
    SessionStore.reset_for_tests()
    ApprovalRegistry.reset_for_tests()
    MetricsCollector.reset_for_tests()


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
    rbac_users = kwargs.get("rbac_users", [])
    settings = kwargs.get(
        "settings",
        ServeSettings(
            enable_control=True,
            enable_turn_start=True,
            rbac=ServeRbacSettings(enabled=bool(rbac_users), users=rbac_users),
        ),
    )
    handler.ctx = kwargs.get(
        "ctx",
        ServeContext(
            store=kwargs.get("store", ThreadStore()),
            run_store=MagicMock(),
            settings=settings,
            auth_token=kwargs.get("auth_token", "legacy-admin"),
            session_store=SessionStore.global_store(),
            rbac_users=rbac_users,
        ),
    )
    return handler


def _viewer_user() -> RbacUser:
    return RbacUser(name="bob", token_hash=hash_token("bob-secret"), role="viewer")


def _operator_user() -> RbacUser:
    return RbacUser(name="alice", token_hash=hash_token("alice-secret"), role="operator")


def test_hash_token_format() -> None:
    h = hash_token("test")
    assert h.startswith("sha256:")


def test_rbac_role_matrix() -> None:
    viewer = resolve_principal_from_token(
        "bob-secret",
        rbac_enabled=True,
        rbac_users=[_viewer_user()],
        legacy_auth_token="",
    )
    assert viewer is not None
    assert viewer.has_permission(Permission.READ)
    assert not viewer.has_permission(Permission.START_TURN)

    operator = resolve_principal_from_token(
        "alice-secret",
        rbac_enabled=True,
        rbac_users=[_operator_user()],
        legacy_auth_token="",
    )
    assert operator is not None
    assert operator.has_permission(Permission.START_TURN)
    assert operator.has_permission(Permission.APPROVE)


def test_viewer_forbidden_run() -> None:
    handler = _handler(
        command="POST",
        path="/threads/abc/run",
        headers={
            "Authorization": "Bearer bob-secret",
            "Content-Length": "20",
        },
        body=b'{"prompt":"hi"}',
        rbac_users=[_viewer_user()],
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 403


def test_operator_can_run() -> None:
    from agent.models import Thread
    from agent.serve.turn_runner import TurnRunHandle, TurnRunner

    thread = Thread(id="abc12345", cwd=str(Path.cwd()), model="m")

    class FakeStore(ThreadStore):
        def load_thread(self, thread_id: str):
            return thread

    handler = _handler(
        command="POST",
        path="/threads/abc12345/run",
        headers={
            "Authorization": "Bearer alice-secret",
            "Content-Length": "20",
        },
        body=b'{"prompt":"hi"}',
        store=FakeStore(),
        rbac_users=[_operator_user()],
    )
    TurnRunner.reset_for_tests()
    with patch.object(
        TurnRunner,
        "start_turn",
        return_value=(TurnRunHandle(thread_id="abc12345", turn_id="t1", status="started"), None),
    ):
        handler.do_POST()
    assert handler.send_response.call_args[0][0] == 200


def test_session_login_and_authorize() -> None:
    store = SessionStore.global_store(ttl_sec=3600)
    users = [_operator_user()]
    rec, err = store.login(
        "alice-secret",
        client_key="127.0.0.1",
        rbac_enabled=True,
        rbac_users=users,
        legacy_auth_token="",
    )
    assert err is None
    assert rec is not None
    result = authorize_request_v2(
        "/threads",
        {"Authorization": f"Session {rec.session_id}"},
        auth_token="",
        auth_mode="session",
        rbac_enabled=True,
        rbac_users=users,
        session_store=store,
        method="GET",
    )
    assert result.authorized is True
    assert result.principal is not None
    assert result.principal.role == "operator"


def test_session_ttl_expired() -> None:
    store = SessionStore.global_store(ttl_sec=0)
    users = [_operator_user()]
    rec, _ = store.login(
        "alice-secret",
        client_key="127.0.0.1",
        rbac_enabled=True,
        rbac_users=users,
        legacy_auth_token="",
    )
    assert rec is not None
    assert store.get_session(rec.session_id) is None


def test_login_rate_limit() -> None:
    store = SessionStore.global_store()
    users = [_operator_user()]
    for _ in range(6):
        store.record_failed_login("bad-ip")
    rec, err = store.login(
        "wrong",
        client_key="bad-ip",
        rbac_enabled=True,
        rbac_users=users,
        legacy_auth_token="",
    )
    assert rec is None
    assert "Too many" in (err or "")


def test_auth_login_endpoint() -> None:
    handler = _handler(
        command="POST",
        path="/auth/login",
        headers={"Content-Length": "28"},
        body=json.dumps({"token": "alice-secret"}).encode(),
        rbac_users=[_operator_user()],
    )
    handler.ctx.settings.auth_mode = "session"
    handler.do_POST()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert "session_id" in body
    assert body["role"] == "operator"


def test_approval_records_approver() -> None:
    reg = ApprovalRegistry.global_registry()
    pending = reg.create(
        thread_id="t1", turn_id="turn1", summary="run", tool_name="run_command"
    )
    handler = _handler(
        command="POST",
        path=f"/approvals/{pending.approval_id}",
        headers={"Authorization": "Bearer alice-secret", "Content-Length": "22"},
        body=b'{"decision":"accept"}',
        rbac_users=[_operator_user()],
    )
    handler.do_POST()
    assert pending.approved_by == "alice"
    assert pending.approved_by_role == "operator"


def test_dashboard_viewer_role_disables_run() -> None:
    from agent.serve.dashboard import render_dashboard_html

    html = render_dashboard_html(role="viewer", user_name="bob", session_mode=True)
    assert "Role: viewer" in html
    assert "disabled" in html


def test_tls_self_signed_integration(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    from agent.serve.tls import generate_self_signed_cert, load_ssl_context

    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    generate_self_signed_cert(cert, key, host="127.0.0.1")
    ctx = load_ssl_context(cert, key)

    settings = ServeSettings(port=0, tls=ServeSettings().tls)
    settings.tls.enabled = True
    settings.tls.cert_file = str(cert)
    settings.tls.key_file = str(key)
    settings.enable_turn_start = False

    server_ref = {}

    def run_server() -> None:
        import socket
        from http.server import ThreadingHTTPServer

        from agent.serve.server import AgentHttpHandler, ServeContext

        store = ThreadStore()
        sctx = ServeContext(
            store=store,
            run_store=MagicMock(),
            settings=settings,
            auth_token="",
            rbac_users=[],
        )

        class H(AgentHttpHandler):
            pass

        H.ctx = sctx
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = ThreadingHTTPServer(("127.0.0.1", port), H)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        server_ref["port"] = port
        server_ref["server"] = server
        server.serve_forever()

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    import time

    for _ in range(50):
        if "port" in server_ref:
            break
        time.sleep(0.05)
    port = server_ref["port"]
    conn = HTTPSConnection("127.0.0.1", port, context=ssl._create_unverified_context())
    conn.request("GET", "/threads")
    resp = conn.getresponse()
    assert resp.status == 200
    server_ref["server"].shutdown()


def test_permission_for_route_read_vs_run() -> None:
    from agent.serve.rbac import permission_for_route

    assert permission_for_route("GET", "/threads") == Permission.READ
    assert permission_for_route("POST", "/threads/x/run") == Permission.START_TURN


def test_legacy_bearer_still_works_when_rbac_off() -> None:
    result = authorize_request_v2(
        "/threads",
        {"Authorization": "Bearer legacy-admin"},
        auth_token="legacy-admin",
        auth_mode="bearer",
        rbac_enabled=False,
        method="GET",
    )
    assert result.authorized is True
