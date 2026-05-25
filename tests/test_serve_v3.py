from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config import Config
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.models import Thread, Turn, UserMessageItem
from agent.serve.approvals import ApprovalRegistry, map_api_decision
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.serve.turn_runner import TurnRunHandle, TurnRunner
from agent.settings import ServeSettings
from agent.store import ThreadStore
from approval.gate import HttpApprovalBridge, prompt_approval, set_http_approval_bridge


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
            store=kwargs.get("store", ThreadStore()),
            run_store=MagicMock(),
            settings=kwargs.get("settings", ServeSettings(enable_control=True, enable_turn_start=True)),
            auth_token=kwargs.get("auth_token", "tok"),
        ),
    )
    return handler


def test_map_api_decision_variants() -> None:
    assert map_api_decision("accept") == "y"
    assert map_api_decision("deny") == "n"
    assert map_api_decision("accept_turn") == "a"
    assert map_api_decision("accept_session") == "A"
    assert map_api_decision("bogus") is None


def test_prometheus_metrics_endpoint() -> None:
    MetricsCollector.reset_for_tests()
    MetricsCollector.global_collector().inc_labeled("agent_turns_total", "completed", 1)
    handler = _handler(
        path="/metrics/prometheus",
        headers={"Authorization": "Bearer tok"},
        auth_token="tok",
    )
    handler.do_GET()
    handler.wfile.seek(0)
    body = handler.wfile.read().decode()
    assert "# TYPE agent_turns_total counter" in body
    assert 'status="completed"' in body


def test_turn_start_disabled() -> None:
    handler = _handler(
        path="/threads/abc/run",
        headers={"Authorization": "Bearer tok", "Content-Length": "20"},
        body=b'{"prompt":"hi"}',
        settings=ServeSettings(enable_control=True, enable_turn_start=False),
        auth_token="tok",
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 403


def test_turn_start_requires_prompt() -> None:
    handler = _handler(
        path="/threads/abc/run",
        headers={"Authorization": "Bearer tok", "Content-Length": "2"},
        body=b"{}",
        auth_token="tok",
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 400


def test_turn_start_success() -> None:
    TurnRunner.reset_for_tests()
    thread = Thread(id="abc12345", cwd=str(Path.cwd()), model="m")
    turn = Turn()
    turn.items.append(UserMessageItem(text="hi"))
    thread.turns.append(turn)

    class FakeStore(ThreadStore):
        def load_thread(self, thread_id: str):
            return thread

    handler = _handler(
        path="/threads/abc12345/run",
        headers={"Authorization": "Bearer tok", "Content-Length": "25"},
        body=b'{"prompt":"Fix tests"}',
        store=FakeStore(),
        auth_token="tok",
    )

    with patch.object(
        TurnRunner,
        "start_turn",
        return_value=(TurnRunHandle(thread_id="abc12345", turn_id="turn-x", status="started"), None),
    ):
        handler.do_POST()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["status"] == "started"
    assert body["turn_id"] == "turn-x"


def test_turn_start_concurrency_cap() -> None:
    TurnRunner.reset_for_tests()
    thread = Thread(id="abc12345", cwd=str(Path.cwd()), model="m")

    class FakeStore(ThreadStore):
        def load_thread(self, thread_id: str):
            return thread

    handler = _handler(
        path="/threads/abc12345/run",
        headers={"Authorization": "Bearer tok", "Content-Length": "20"},
        body=b'{"prompt":"one"}',
        store=FakeStore(),
        settings=ServeSettings(enable_control=True, enable_turn_start=True, max_concurrent_turns=1),
        auth_token="tok",
    )
    with patch.object(
        TurnRunner,
        "start_turn",
        return_value=(None, "max concurrent turns (1) reached"),
    ):
        handler.do_POST()
    assert handler.send_response.call_args[0][0] == 429


def test_approval_resolve_accept() -> None:
    ApprovalRegistry.reset_for_tests()
    reg = ApprovalRegistry.global_registry()
    pending = reg.create(
        thread_id="t1",
        turn_id="turn1",
        summary="run_command",
        tool_name="run_command",
    )
    handler = _handler(
        path=f"/approvals/{pending.approval_id}",
        headers={"Authorization": "Bearer tok", "Content-Length": "22"},
        body=b'{"decision":"accept"}',
        auth_token="tok",
    )
    handler.do_POST()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["ok"] is True
    assert pending.decision == "accept"


def test_approval_invalid_decision() -> None:
    handler = _handler(
        path="/approvals/appr-abc",
        headers={"Authorization": "Bearer tok", "Content-Length": "22"},
        body=b'{"decision":"maybe"}',
        auth_token="tok",
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 400


def test_approval_not_found() -> None:
    ApprovalRegistry.reset_for_tests()
    handler = _handler(
        path="/approvals/missing",
        headers={"Authorization": "Bearer tok", "Content-Length": "20"},
        body=b'{"decision":"deny"}',
        auth_token="tok",
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 404


def test_sync_resolve_wires_service(tmp_path: Path) -> None:
    body = json.dumps(
        {"path": "missing.txt", "strategy": "local-wins", "cwd": str(tmp_path)}
    ).encode()
    handler = _handler(
        path="/sync/resolve",
        headers={"Authorization": "Bearer tok", "Content-Length": str(len(body))},
        body=body,
        auth_token="tok",
    )
    handler.do_POST()
    handler.wfile.seek(0)
    payload = json.loads(handler.wfile.read().decode())
    assert "ok" in payload


def test_dashboard_when_turn_start_enabled() -> None:
    handler = _handler(
        path="/",
        headers={"Authorization": "Bearer tok"},
        settings=ServeSettings(enable_turn_start=True),
        auth_token="tok",
    )
    handler.do_GET()
    handler.wfile.seek(0)
    html = handler.wfile.read().decode()
    assert "agent-cli dashboard" in html
    assert "runBtn" in html


def test_metrics_json_includes_labeled() -> None:
    MetricsCollector.reset_for_tests()
    MetricsCollector.global_collector().inc_labeled("agent_tool_calls_total", "read_file", 2)
    handler = _handler(
        path="/metrics",
        headers={"Authorization": "Bearer tok"},
        auth_token="tok",
    )
    handler.do_GET()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["labeled_counters"]["agent_tool_calls_total"]["read_file"] == 2


def test_turn_start_auth_required() -> None:
    handler = _handler(
        path="/threads/abc/run",
        headers={"Content-Length": "20"},
        body=b'{"prompt":"hi"}',
        auth_token="tok",
    )
    handler.do_POST()
    handler.wfile.seek(0)
    body = json.loads(handler.wfile.read().decode())
    assert body["error"] == "Unauthorized"


def test_approval_registry_wait_timeout() -> None:
    ApprovalRegistry.reset_for_tests()
    reg = ApprovalRegistry.global_registry()
    pending = reg.create(
        thread_id="t1",
        turn_id="turn1",
        summary="x",
        tool_name="read_file",
    )
    assert reg.wait(pending.approval_id, timeout=0.01) is None


def test_http_bridge_prompt_approval() -> None:
    ApprovalRegistry.reset_for_tests()
    emitted: list[tuple[str, str, str]] = []

    bridge = HttpApprovalBridge(
        thread_id="t1",
        turn_id="turn1",
        timeout_sec=1,
        emit=lambda aid, tool, summary: emitted.append((aid, tool, summary)),
    )
    set_http_approval_bridge(bridge)

    def _resolve():
        import time

        time.sleep(0.05)
        reg = ApprovalRegistry.global_registry()
        aid = emitted[0][0]
        reg.resolve(aid, "accept")

    import threading

    t = threading.Thread(target=_resolve)
    t.start()
    ok = prompt_approval("read_file", {"path": "x.py"})
    t.join()
    set_http_approval_bridge(None)
    assert ok is True
    assert emitted


def test_turn_runner_active_count() -> None:
    TurnRunner.reset_for_tests()
    runner = TurnRunner.global_runner()
    assert runner.active_count() == 0


def test_control_disabled_blocks_cancel() -> None:
    ActiveTurnRegistry.reset_for_tests()
    token = __import__("agent.cancel", fromlist=["CancelToken"]).CancelToken()
    ActiveTurnRegistry.global_registry().register("abc", "turn1", token)
    handler = _handler(
        path="/threads/abc/cancel",
        headers={"Authorization": "Bearer tok"},
        settings=ServeSettings(enable_control=False),
        auth_token="tok",
    )
    handler.do_POST()
    assert handler.send_response.call_args[0][0] == 403


def test_sse_with_turn_id_query() -> None:
    from agent.models import Thread, Turn, UserMessageItem

    thread = Thread(id="abc12345", cwd="/tmp", model="m")
    turn = Turn(id="turn-q")
    turn.items.append(UserMessageItem(text="hi"))
    thread.turns.append(turn)

    class FakeStore(ThreadStore):
        def load_thread(self, thread_id: str):
            return thread

    run_store = MagicMock()
    run_store.load_events.return_value = []

    handler = _handler(
        path="/threads/abc12345/events?turn_id=turn-q",
        headers={},
        auth_token="",
    )
    handler.ctx = ServeContext(
        store=FakeStore(),
        run_store=run_store,
        settings=ServeSettings(stream_buffer_size=2),
        auth_token="",
    )
    handler.do_GET()
    run_store.load_events.assert_called()
    assert run_store.load_events.call_args[0][0] == "turn-q"


def test_prompt_approval_http_deny() -> None:
    ApprovalRegistry.reset_for_tests()
    emitted: list[str] = []

    bridge = HttpApprovalBridge(
        thread_id="t1",
        turn_id="turn1",
        timeout_sec=1,
        emit=lambda aid, tool, summary: emitted.append(aid),
    )
    set_http_approval_bridge(bridge)

    def _resolve():
        import time

        time.sleep(0.05)
        ApprovalRegistry.global_registry().resolve(emitted[0], "deny")

    import threading

    threading.Thread(target=_resolve).start()
    ok = prompt_approval("write_file", {"path": "a.py", "content": "x"})
    set_http_approval_bridge(None)
    assert ok is False
