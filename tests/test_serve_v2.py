from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock

from agent.cancel import CancelToken
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.serve.auth import authorize_request, extract_bearer_token, extract_query_token
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.settings import ServeSettings
from agent.store import ThreadStore


def _handler(**kwargs) -> AgentHttpHandler:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.path = kwargs.get("path", "/")
    handler.headers = kwargs.get("headers", {})
    handler.wfile = BytesIO()
    handler.rfile = BytesIO(kwargs.get("body", b""))
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.ctx = kwargs.get(
        "ctx",
        ServeContext(
            store=ThreadStore(),
            run_store=MagicMock(),
            settings=ServeSettings(enable_control=True),
            auth_token=kwargs.get("auth_token", "tok"),
        ),
    )
    return handler


def test_extract_bearer_token() -> None:
    assert extract_bearer_token({"Authorization": "Bearer secret"}) == "secret"


def test_extract_query_token() -> None:
    assert extract_query_token("/threads?token=abc") == "abc"


def test_authorize_without_token() -> None:
    ok, err = authorize_request("/threads", {}, auth_token="")
    assert ok is True
    assert err is None


def test_authorize_rejects_missing_token() -> None:
    ok, err = authorize_request("/threads", {}, auth_token="secret")
    assert ok is False
    assert err == "Unauthorized"


def test_authorize_accepts_bearer() -> None:
    ok, err = authorize_request(
        "/threads",
        {"Authorization": "Bearer secret"},
        auth_token="secret",
    )
    assert ok is True


def test_metrics_snapshot() -> None:
    MetricsCollector.reset_for_tests()
    m = MetricsCollector.global_collector()
    m.inc("turns_started")
    snap = m.snapshot()
    assert snap.counters["turns_started"] == 1


def test_active_turn_cancel() -> None:
    ActiveTurnRegistry.reset_for_tests()
    reg = ActiveTurnRegistry.global_registry()
    token = CancelToken()
    reg.register("thread1", "turn1", token)
    assert reg.is_active("thread1")
    assert reg.cancel("thread1")
    assert token.cancelled
    reg.unregister("thread1")
    assert not reg.is_active("thread1")


def test_handler_json_threads_auth_required() -> None:
    handler = _handler(path="/threads", headers={}, auth_token="tok")
    handler.do_GET()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["error"] == "Unauthorized"


def test_handler_metrics_with_token() -> None:
    MetricsCollector.reset_for_tests()
    MetricsCollector.global_collector().inc("turns_completed", 2)
    handler = _handler(
        path="/metrics",
        headers={"Authorization": "Bearer tok"},
        auth_token="tok",
    )
    handler.do_GET()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["counters"]["turns_completed"] == 2


def test_handler_cancel_active_turn() -> None:
    ActiveTurnRegistry.reset_for_tests()
    token = CancelToken()
    ActiveTurnRegistry.global_registry().register("abc12345", "turn1", token)
    handler = _handler(
        path="/threads/abc12345/cancel",
        headers={"Authorization": "Bearer tok"},
        auth_token="tok",
    )
    handler.do_POST()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["ok"] is True
    assert token.cancelled


def test_handler_cancel_no_active() -> None:
    ActiveTurnRegistry.reset_for_tests()
    handler = _handler(
        path="/threads/missing/cancel",
        headers={"Authorization": "Bearer tok"},
        auth_token="tok",
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 404


def test_sse_content_type() -> None:
    from agent.models import Thread, Turn, UserMessageItem

    thread = Thread(id="abc12345", cwd="/tmp", model="m")
    turn = Turn()
    turn.items.append(UserMessageItem(text="hi"))
    thread.turns.append(turn)

    class FakeStore(ThreadStore):
        def load_thread(self, thread_id: str):
            return thread

        def list_threads(self):
            return [thread]

    run_store = MagicMock()
    run_store.load_events.return_value = []

    handler = _handler(
        path="/threads/abc12345/events",
        headers={},
        auth_token="",
    )
    handler.ctx = ServeContext(
        store=FakeStore(),
        run_store=run_store,
        settings=ServeSettings(),
        auth_token="",
    )
    handler.do_GET()
    types = [c[0][0] for c in handler.send_header.call_args_list]
    assert "Content-Type" in types
