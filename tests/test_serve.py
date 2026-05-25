import json
import threading
from http.client import HTTPConnection

from agent.models import Thread, Turn, UserMessageItem
from agent.recording.store import RunStore
from agent.serve.server import AgentHttpHandler, ThreadingHTTPServer, _thread_to_dict

from agent.store import ThreadStore


def test_http_homepage_html(tmp_path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="thread-abc12345", cwd=str(tmp_path), model="m", title="demo")
    store.create_thread(thread)

    class Handler(AgentHttpHandler):
        pass

    Handler.store = store
    Handler.run_store = RunStore(base_dir=tmp_path / "runs")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread_srv = threading.Thread(target=server.serve_forever, daemon=True)
    thread_srv.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode()
        assert "<h1>Threads</h1>" in body
        assert "demo" in body
    finally:
        server.shutdown()


def test_http_threads_list(tmp_path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="thread-abc12345", cwd=str(tmp_path), model="m", title="demo")
    turn = Turn()
    turn.items = [UserMessageItem(text="hello")]
    thread.turns.append(turn)
    store.create_thread(thread)

    class Handler(AgentHttpHandler):
        pass

    Handler.store = store
    Handler.run_store = RunStore(base_dir=tmp_path / "runs")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread_srv = threading.Thread(target=server.serve_forever, daemon=True)
    thread_srv.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/threads")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert len(data) == 1
        assert data[0]["id"] == "thread-abc12345"
        assert data[0]["title"] == "demo"
    finally:
        server.shutdown()


def test_thread_to_dict() -> None:
    thread = Thread(id="t1", cwd="/tmp", model="m")
    turn = Turn()
    turn.items = [UserMessageItem(text="hello")]
    thread.turns.append(turn)
    data = _thread_to_dict(thread)
    assert data["id"] == "t1"
    assert len(data["turns"]) == 1
    assert data["turns"][0]["items"][0]["type"] == "userMessage"
