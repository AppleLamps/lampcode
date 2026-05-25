from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.metrics import MetricsCollector
from agent.models import FileChangeItem, Thread, Turn, new_id
from agent.serve.ide import (
    compute_diff_gutter,
    enforce_tab_limit,
    file_history_from_thread,
    tabs_state_snapshot,
)
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.settings import RbacUser, ServeIdeSettings, ServeRbacSettings, ServeSettings
from agent.store import ThreadStore
from agent.serve.rbac import hash_token


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    yield
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
            enable_turn_start=True,
            rbac=ServeRbacSettings(enabled=True, users=rbac_users),
            ide=ServeIdeSettings(enabled=True, max_open_tabs=3, show_diff_gutter=True),
        ),
    )
    store = kwargs.get("store", ThreadStore())
    handler.ctx = ServeContext(
        store=store,
        run_store=MagicMock(),
        settings=settings,
        auth_token="legacy",
        session_store=kwargs.get("session_store"),
        rbac_users=rbac_users,
    )
    handler.principal = kwargs.get("principal")
    return handler


def _thread_with_file_change(tmp_path: Path, store: ThreadStore) -> Thread:
    t = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    turn = Turn()
    turn.items.append(
        FileChangeItem(
            path="src/app.py",
            status="completed",
            change_type="update",
            diff_snippet="old line\nremoved\n",
            summary="updated app",
        )
    )
    t.turns.append(turn)
    store.create_thread(t)
    store.append_turn(t, turn)
    for item in turn.items:
        store.append_item(t, turn.id, item)
    store.save_thread(t)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("old line\nnew line\n", encoding="utf-8")
    return t


def test_file_history_from_thread() -> None:
    t = Thread(id=new_id(), cwd=".", model="m")
    turn = Turn()
    turn.items.append(FileChangeItem(path="a.py", status="completed", diff_snippet="+x"))
    t.turns.append(turn)
    hist = file_history_from_thread(t, "a.py")
    assert len(hist) == 1
    assert hist[0]["path"] == "a.py"


def test_compute_diff_gutter_enabled() -> None:
    history = [{"diff_snippet": "line1\nline2\n", "turn_id": "t1"}]
    gutter = compute_diff_gutter("line1\nline3\n", history)
    assert gutter["enabled"] is True
    assert isinstance(gutter["added_lines"], list)


def test_compute_diff_gutter_disabled_without_snippet() -> None:
    gutter = compute_diff_gutter("content", [{"turn_id": "t"}])
    assert gutter["enabled"] is False


def test_enforce_tab_limit() -> None:
    tabs, trimmed = enforce_tab_limit(["a", "b", "c", "d"], max_tabs=3)
    assert trimmed is True
    assert tabs == ["b", "c", "d"]


def test_enforce_tab_limit_under() -> None:
    tabs, trimmed = enforce_tab_limit(["a"], max_tabs=10)
    assert trimmed is False
    assert tabs == ["a"]


def test_tabs_state_snapshot() -> None:
    snap = tabs_state_snapshot(["a.py", "b.py"], "a.py")
    assert snap["active"] == "a.py"
    assert len(snap["open_tabs"]) == 2


def test_ide_history_endpoint(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t = _thread_with_file_change(tmp_path, store)
    op = RbacUser(name="o", token_hash=hash_token("secret"), role="operator")
    h = _handler(
        path=f"/ide/history?thread_id={t.id}&path=src/app.py",
        headers={"Authorization": "Bearer secret"},
        rbac_users=[op],
        store=store,
    )
    h.do_GET_inner()
    body = json.loads(h.wfile.getvalue().decode())
    assert body["history"]
    assert "gutter" in body


def test_ide_tabs_state_trimmed(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    store.create_thread(t)
    op = RbacUser(name="o", token_hash=hash_token("secret"), role="operator")
    h = _handler(
        path=f"/ide/tabs/state?thread_id={t.id}&tabs=a,b,c,d",
        headers={"Authorization": "Bearer secret"},
        rbac_users=[op],
        store=store,
    )
    h.do_GET_inner()
    body = json.loads(h.wfile.getvalue().decode())
    assert body["trimmed"] is True
    assert len(body["open_tabs"]) == 3


def test_ide_viewer_cannot_put(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    store.create_thread(t)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    viewer = RbacUser(name="v", token_hash=hash_token("v-secret"), role="viewer")
    h = _handler(
        command="PUT",
        path=f"/ide/file?thread_id={t.id}&path=f.txt",
        headers={"Authorization": "Bearer v-secret", "Content-Length": "15"},
        body=b'{"content":"y"}',
        rbac_users=[viewer],
        store=store,
    )
    from agent.serve.rbac import AuthPrincipal

    h.principal = AuthPrincipal(name="v", role="viewer", auth_method="bearer")
    h.do_PUT_inner()
    assert h.send_response.called


def test_dashboard_includes_tabs(tmp_path: Path) -> None:
    from agent.serve.dashboard import render_dashboard_html

    html = render_dashboard_html(ide_enabled=True, role="operator", max_open_tabs=5)
    assert "ideTabs" in html
    assert "IDE_MAX_TABS = 5" in html


def test_serve_ide_settings_defaults() -> None:
    ide = ServeIdeSettings()
    assert ide.max_open_tabs == 10
    assert ide.show_diff_gutter is True
    assert ide.autosave is False


def test_history_empty_path(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    store.create_thread(t)
    op = RbacUser(name="o", token_hash=hash_token("secret"), role="operator")
    h = _handler(
        path=f"/ide/history?thread_id={t.id}&path=missing.py",
        headers={"Authorization": "Bearer secret"},
        rbac_users=[op],
        store=store,
    )
    h.do_GET_inner()
    body = json.loads(h.wfile.getvalue().decode())
    assert body["history"] == []


def test_gutter_diff_added_removed_lines() -> None:
    old = "a\nb\nc\n"
    new = "a\nx\nc\n"
    gutter = compute_diff_gutter(new, [{"diff_snippet": old}])
    assert gutter["enabled"] is True


def test_enforce_tab_limit_zero_means_unlimited() -> None:
    tabs, trimmed = enforce_tab_limit(["a", "b"], max_tabs=0)
    assert tabs == ["a", "b"]
    assert trimmed is False


def test_ide_history_requires_thread_id() -> None:
    h = _handler(path="/ide/history?path=a.py", headers={"Authorization": "Bearer legacy"})
    h.do_GET_inner()
    assert h.send_response.called


def test_metrics_tabs_gauge_on_tabs_state(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    store.create_thread(t)
    op = RbacUser(name="o", token_hash=hash_token("secret"), role="operator")
    h = _handler(
        path=f"/ide/tabs/state?thread_id={t.id}&tabs=a,b",
        headers={"Authorization": "Bearer secret"},
        rbac_users=[op],
        store=store,
    )
    h.do_GET_inner()
    assert MetricsCollector.global_collector().snapshot().gauges.get("agent_ide_tabs_open") == 2


def test_multiple_file_changes_history_limit() -> None:
    t = Thread(id=new_id(), cwd=".", model="m")
    turn = Turn()
    for i in range(5):
        turn.items.append(FileChangeItem(path="f.py", status="completed", summary=str(i)))
    t.turns.append(turn)
    hist = file_history_from_thread(t, "f.py", limit=3)
    assert len(hist) == 3


def test_diff_endpoint_still_works(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t = _thread_with_file_change(tmp_path, store)
    op = RbacUser(name="o", token_hash=hash_token("secret"), role="operator")
    h = _handler(
        path=f"/ide/diff?thread_id={t.id}&path=src/app.py",
        headers={"Authorization": "Bearer secret"},
        rbac_users=[op],
        store=store,
    )
    h.do_GET_inner()
    body = json.loads(h.wfile.getvalue().decode())
    assert body["path"] == "src/app.py"
