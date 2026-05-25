from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.metrics import MetricsCollector
from agent.serve.ide_diagnostics import (
    DiagnosticItem,
    parse_ruff_json_output,
    run_diagnostics,
)
from agent.serve.rbac import hash_token
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.settings import RbacUser, ServeIdeDiagnosticsSettings, ServeIdeSettings, ServeRbacSettings, ServeSettings
from agent.store import ThreadStore


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    yield
    MetricsCollector.reset_for_tests()


def test_parse_ruff_json_output() -> None:
    raw = json.dumps(
        [
            {
                "code": "E999",
                "message": "Syntax error",
                "location": {"row": 10, "column": 3},
            }
        ]
    )
    items = parse_ruff_json_output(raw)
    assert len(items) == 1
    assert items[0]["line"] == 10
    assert items[0]["severity"] == "error"


def test_parse_ruff_invalid_json() -> None:
    assert parse_ruff_json_output("not json") == []


def test_diagnostic_item_to_dict() -> None:
    d = DiagnosticItem(1, 2, "warning", "msg")
    assert d.to_dict()["severity"] == "warning"


def test_run_diagnostics_disabled(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x=1\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=False)
    assert run_diagnostics(f, settings=settings) == []


def test_run_diagnostics_py_compile(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("def broken(\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=True, python_tool="py_compile", timeout_sec=5)
    items = run_diagnostics(f, settings=settings, shutil_which=lambda _: None)
    assert items
    assert items[0]["severity"] == "error"


def test_run_diagnostics_ruff_mocked(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("x=1\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=True, python_tool="ruff")
    raw = json.dumps([{"code": "E501", "message": "line too long", "location": {"row": 1, "column": 1}}])
    with patch("agent.serve.ide_diagnostics.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout=raw, stderr="")
        items = run_diagnostics(f, settings=settings)
    assert items[0]["message"] == "line too long"


def test_run_diagnostics_timeout(tmp_path: Path) -> None:
    import subprocess

    f = tmp_path / "slow.py"
    f.write_text("x=1\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=True, python_tool="py_compile", timeout_sec=1)
    with patch("agent.serve.ide_diagnostics.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
        items = run_diagnostics(f, settings=settings, shutil_which=lambda _: None)
    assert items[0]["severity"] == "error"


def test_run_diagnostics_max_limit(tmp_path: Path) -> None:
    f = tmp_path / "many.py"
    f.write_text("x=1\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=True, python_tool="none", max_diagnostics=2)
    assert run_diagnostics(f, settings=settings) == []


def test_run_diagnostics_missing_file(tmp_path: Path) -> None:
    settings = ServeIdeDiagnosticsSettings(enabled=True)
    assert run_diagnostics(tmp_path / "nope.py", settings=settings) == []


def test_js_eslint_none_by_default(tmp_path: Path) -> None:
    f = tmp_path / "app.js"
    f.write_text("console.log(1)\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=True, js_tool="none")
    assert run_diagnostics(f, settings=settings) == []


def _handler(tmp_path: Path, store: ThreadStore, **kwargs) -> AgentHttpHandler:
    t = kwargs.get("thread")
    op = RbacUser(name="v", token_hash=hash_token("secret"), role="viewer")
    settings = ServeSettings(
        rbac=ServeRbacSettings(enabled=True, users=[op]),
        ide=ServeIdeSettings(
            enabled=True,
            diagnostics=ServeIdeDiagnosticsSettings(enabled=True, python_tool="none"),
        ),
    )
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "GET"
    tid = t.id if t else "missing"
    handler.path = kwargs.get("path", f"/ide/diagnostics?thread_id={tid}&path=src/a.py")
    handler.headers = kwargs.get("headers", {"Authorization": "Bearer secret"})
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler._json_response = MagicMock(side_effect=lambda body, status=200: handler.wfile.write(json.dumps(body).encode()))
    handler._error = MagicMock()
    handler.ctx = ServeContext(
        store=store,
        run_store=MagicMock(),
        settings=settings,
        auth_token="legacy",
        rbac_users=[op],
    )
    handler.principal = kwargs.get("principal")
    return handler


def test_diagnostics_endpoint_viewer(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
    from agent.models import Thread, new_id

    t = Thread(id=new_id(), cwd=str(proj), model="m")
    store.create_thread(t)
    h = _handler(tmp_path, store, thread=t)
    h.do_GET_inner()
    body = json.loads(h.wfile.getvalue().decode())
    assert "items" in body


def test_diagnostics_path_jail(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "proj" / "threads")
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    outside = tmp_path / "outside.py"
    outside.write_text("x=1\n", encoding="utf-8")
    from agent.models import Thread, new_id

    t = Thread(id=new_id(), cwd=str(proj), model="m")
    store.create_thread(t)
    h = _handler(
        tmp_path,
        store,
        thread=t,
        path=f"/ide/diagnostics?thread_id={t.id}&path=../outside.py",
    )
    h.do_GET_inner()
    h._error.assert_called()


def test_diagnostics_disabled_returns_empty(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
    from agent.models import Thread, new_id

    t = Thread(id=new_id(), cwd=str(proj), model="m")
    store.create_thread(t)
    op = RbacUser(name="v", token_hash=hash_token("secret"), role="viewer")
    settings = ServeSettings(
        rbac=ServeRbacSettings(enabled=True, users=[op]),
        ide=ServeIdeSettings(enabled=True, diagnostics=ServeIdeDiagnosticsSettings(enabled=False)),
    )
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "GET"
    handler.path = f"/ide/diagnostics?thread_id={t.id}&path=src/a.py"
    handler.headers = {"Authorization": "Bearer secret"}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    def capture(body, status=200):
        handler.wfile.write(json.dumps(body).encode())

    handler._json_response = capture
    handler._error = MagicMock()
    handler.ctx = ServeContext(
        store=store,
        run_store=MagicMock(),
        settings=settings,
        auth_token="legacy",
        rbac_users=[op],
    )
    handler.do_GET_inner()
    body = json.loads(handler.wfile.getvalue().decode())
    assert body["items"] == []


def test_auto_tool_selection_ruff(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x=1\n", encoding="utf-8")
    settings = ServeIdeDiagnosticsSettings(enabled=True, python_tool="auto")
    with patch("agent.serve.ide_diagnostics.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        run_diagnostics(f, settings=settings, shutil_which=lambda n: "ruff" if n == "ruff" else None)
    run.assert_called()
