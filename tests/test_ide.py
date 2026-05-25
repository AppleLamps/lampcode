from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.metrics import MetricsCollector
from agent.serve.auth import authorize_request_v2
from agent.serve.ide import (
    IdeError,
    list_tree,
    read_file,
    safe_resolve,
    write_file_atomic,
)
from agent.serve.rbac import Permission, hash_token
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.settings import RbacUser, ServeIdeSettings, ServeRbacSettings, ServeSettings
from agent.models import Thread, new_id
from agent.store import ThreadStore


def _make_thread(store: ThreadStore, tmp_path: Path) -> Thread:
    t = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    store.create_thread(t)
    return t


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
            ide=ServeIdeSettings(enabled=True),
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
    return handler


def _viewer() -> RbacUser:
    return RbacUser(name="v", token_hash=hash_token("v-secret"), role="viewer")


def _operator() -> RbacUser:
    return RbacUser(name="o", token_hash=hash_token("o-secret"), role="operator")


def test_safe_resolve_within_cwd(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    p = safe_resolve(tmp_path, "a.txt")
    assert p == f.resolve()


def test_safe_resolve_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(IdeError):
        safe_resolve(tmp_path, "../outside")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(IdeError):
        safe_resolve(tmp_path, "link.txt")


def test_list_tree_basic(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print()", encoding="utf-8")
    data = list_tree(tmp_path, ".", max_depth=3, max_entries=100)
    assert data["count"] >= 2
    names = {e["name"] for e in data["entries"]}
    assert "src" in names


def test_list_tree_depth_limit(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    data = list_tree(tmp_path, ".", max_depth=2, max_entries=100)
    assert data["count"] >= 1


def test_read_file_ok(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    data = read_file(tmp_path, "f.txt", max_bytes=1024)
    assert data["content"] == "hello"


def test_read_file_too_large(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 20, encoding="utf-8")
    with pytest.raises(IdeError) as exc:
        read_file(tmp_path, "big.txt", max_bytes=5)
    assert exc.value.status == 413


def test_write_atomic(tmp_path: Path) -> None:
    result = write_file_atomic(tmp_path, "new.txt", "data", max_bytes=1024)
    assert result["ok"] is True
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "data"


def test_viewer_can_read_route_permission() -> None:
    r = authorize_request_v2(
        "/ide/file?path=a.py",
        {"Authorization": "Bearer v-secret"},
        auth_token="",
        auth_mode="bearer",
        rbac_enabled=True,
        rbac_users=[_viewer()],
        method="GET",
    )
    assert r.authorized is True
    assert r.required_permission == Permission.READ


def test_viewer_forbidden_put() -> None:
    r = authorize_request_v2(
        "/ide/file?path=a.py",
        {"Authorization": "Bearer v-secret"},
        auth_token="",
        auth_mode="bearer",
        rbac_enabled=True,
        rbac_users=[_viewer()],
        method="PUT",
    )
    assert r.authorized is False


def test_operator_can_put() -> None:
    r = authorize_request_v2(
        "/ide/file?path=a.py",
        {"Authorization": "Bearer o-secret"},
        auth_token="",
        auth_mode="bearer",
        rbac_enabled=True,
        rbac_users=[_operator()],
        method="PUT",
    )
    assert r.authorized is True
    assert r.required_permission == Permission.IDE_WRITE


def test_ide_get_file_via_handler(tmp_path: Path) -> None:
    store = ThreadStore()
    thread = _make_thread(store, tmp_path)
    (tmp_path / "hello.py").write_text("print(1)", encoding="utf-8")
    h = _handler(
        path=f"/ide/file?thread_id={thread.id}&path=hello.py",
        headers={"Authorization": "Bearer o-secret"},
        rbac_users=[_operator()],
        store=store,
    )
    h.do_GET()
    assert h.send_response.call_args[0][0] == 200
    body = json.loads(h.wfile.getvalue().decode())
    assert "print(1)" in body["content"]


def test_ide_put_forbidden_viewer(tmp_path: Path) -> None:
    store = ThreadStore()
    thread = _make_thread(store, tmp_path)
    h = _handler(
        command="PUT",
        path=f"/ide/file?thread_id={thread.id}&path=x.py",
        headers={"Authorization": "Bearer v-secret"},
        rbac_users=[_viewer(), _operator()],
        store=store,
        body=json.dumps({"content": "x"}).encode(),
    )
    h.do_PUT()
    assert h.send_response.call_args[0][0] == 403


def test_ide_put_ok_operator(tmp_path: Path) -> None:
    store = ThreadStore()
    thread = _make_thread(store, tmp_path)
    body = json.dumps({"content": "ok"}).encode()
    h = _handler(
        command="PUT",
        path=f"/ide/file?thread_id={thread.id}&path=x.py",
        headers={"Authorization": "Bearer o-secret", "Content-Length": str(len(body))},
        rbac_users=[_operator()],
        store=store,
        body=body,
    )
    h.do_PUT()
    assert h.send_response.call_args[0][0] == 200
    assert (tmp_path / "x.py").read_text(encoding="utf-8") == "ok"


def test_ide_disabled_404() -> None:
    h = _handler(
        path="/ide/tree?thread_id=x&path=.",
        headers={"Authorization": "Bearer legacy"},
        settings=ServeSettings(
            auth_token="legacy",
            ide=ServeIdeSettings(enabled=False),
            rbac=ServeRbacSettings(enabled=False),
        ),
    )
    h.do_GET()
    assert h.send_response.call_args[0][0] == 404


def test_ide_tree_requires_thread_id() -> None:
    h = _handler(
        path="/ide/tree?path=.",
        headers={"Authorization": "Bearer o-secret"},
        rbac_users=[_operator()],
    )
    h.do_GET()
    assert h.send_response.call_args[0][0] == 400


def test_ide_metrics_on_read(tmp_path: Path) -> None:
    store = ThreadStore()
    thread = _make_thread(store, tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    h = _handler(
        path=f"/ide/file?thread_id={thread.id}&path=a.txt",
        headers={"Authorization": "Bearer o-secret"},
        rbac_users=[_operator()],
        store=store,
    )
    h.do_GET()
    prom = MetricsCollector.global_collector().to_prometheus()
    assert "agent_ide_requests_total" in prom


def test_list_tree_entry_limit(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    data = list_tree(tmp_path, ".", max_depth=1, max_entries=3)
    assert data["count"] <= 3


def test_write_content_limit(tmp_path: Path) -> None:
    with pytest.raises(IdeError):
        write_file_atomic(tmp_path, "x.txt", "a" * 100, max_bytes=10)


def test_read_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IdeError) as exc:
        read_file(tmp_path, "nope.txt", max_bytes=100)
    assert exc.value.status == 404


def test_ide_diff_route(tmp_path: Path) -> None:
    store = ThreadStore()
    thread = _make_thread(store, tmp_path)
    h = _handler(
        path=f"/ide/diff?thread_id={thread.id}&path=missing.py",
        headers={"Authorization": "Bearer o-secret"},
        rbac_users=[_operator()],
        store=store,
    )
    h.do_GET()
    assert h.send_response.call_args[0][0] == 200
    body = json.loads(h.wfile.getvalue().decode())
    assert body.get("diff_snippet") is None


def test_dashboard_includes_ide_when_enabled() -> None:
    from agent.serve.dashboard import render_dashboard_html

    html = render_dashboard_html(ide_enabled=True, role="operator")
    assert "idePanel" in html
    assert "monaco" in html.lower() or "IDE" in html
