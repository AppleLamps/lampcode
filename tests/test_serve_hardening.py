from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock

from agent.models import AgentMessageItem, CommandExecutionItem, FileChangeItem, Thread, Turn
from agent.serve.auth import authorize_request_v2
from agent.serve.http_response import thread_to_redacted_dict
from agent.serve.request_limits import RequestBodyTooLarge, read_limited_body
from agent.serve.rbac import AuthPrincipal
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.settings import ServeSettings, ServeTlsSettings
from agent.store import ThreadStore


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
    handler.principal = kwargs.get("principal")
    handler.ctx = kwargs.get(
        "ctx",
        ServeContext(
            store=kwargs.get("store", ThreadStore()),
            run_store=MagicMock(),
            settings=kwargs.get("settings", ServeSettings()),
            auth_token=kwargs.get("auth_token", "tok"),
        ),
    )
    return handler


def test_query_tokens_disabled_by_default() -> None:
    result = authorize_request_v2("/threads?token=secret", {}, auth_token="secret")
    assert result.authorized is False


def test_query_tokens_legacy_opt_in() -> None:
    result = authorize_request_v2(
        "/threads?token=secret",
        {},
        auth_token="secret",
        allow_query_tokens=True,
    )
    assert result.authorized is True


def test_request_body_limit_rejects_large_content_length() -> None:
    handler = _handler(headers={"Content-Length": "10"}, body=b"0123456789")
    try:
        read_limited_body(handler, max_bytes=5)
    except RequestBodyTooLarge as exc:
        assert exc.actual == 10
    else:  # pragma: no cover
        raise AssertionError("expected RequestBodyTooLarge")


def test_cors_echoes_only_allowed_origin() -> None:
    handler = _handler(
        headers={"Origin": "https://allowed.example"},
        settings=ServeSettings(cors=True, cors_allowed_origins=["https://allowed.example"]),
    )
    handler._json_response({"ok": True})
    headers = [call.args for call in handler.send_header.call_args_list]
    assert ("Access-Control-Allow-Origin", "https://allowed.example") in headers
    assert ("Access-Control-Allow-Origin", "*") not in headers


def test_oidc_cookie_secure_when_tls_enabled() -> None:
    handler = _handler(settings=ServeSettings(tls=ServeTlsSettings(enabled=True)))
    ctx = handler._get_ctx()

    class FakeOidc:
        def validate_state(self, state):
            return object()

        def exchange_code(self, code, state):
            return {"sub": "s", "email": "u@example.test"}

        def principal_from_claims(self, claims):
            return AuthPrincipal(name="u", role="admin", auth_method="oidc")

    class FakeSessionStore:
        def create_session(self, *args, **kwargs):
            return type("Rec", (), {"session_id": "sid"})()

    ctx.oidc_client = FakeOidc()
    ctx.session_store = FakeSessionStore()
    handler.path = "/auth/oidc/callback?code=c&state=s"
    handler._oidc_callback()
    cookies = [call.args[1] for call in handler.send_header.call_args_list if call.args[0] == "Set-Cookie"]
    assert cookies
    assert "; Secure" in cookies[0]


def test_thread_redaction_omits_sensitive_fields() -> None:
    thread = Thread(id="t", cwd="/tmp", model="m")
    turn = Turn()
    turn.items.append(AgentMessageItem(text="secret answer"))
    turn.items.append(CommandExecutionItem(command="cat secret", cwd="/tmp", output="secret output"))
    turn.items.append(FileChangeItem(path="a.txt", content="secret file", diff_snippet="diff"))
    thread.turns.append(turn)
    data = thread_to_redacted_dict(thread)
    dumped = json.dumps(data)
    assert "secret output" not in dumped
    assert "secret file" not in dumped
    assert "diff" not in dumped
    assert data["redacted"] is True