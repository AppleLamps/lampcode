import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.events import EventEmitter
from agent.models import CollabWorkerItem, Thread, Turn, UserMessageItem
from agent.multi_agent.registry import WorkerRegistry, new_worker_id
from agent.settings import MultiAgentSettings
from agent.store import ThreadStore
from model.openrouter import CompletionResult


def _ma_config(tmp_path: Path, **overrides) -> Config:
    ma = MultiAgentSettings(
        enabled=True,
        max_workers_per_turn=5,
        max_worker_depth=2,
        max_concurrent_workers=3,
        worker_auto_approve=True,
        wait_timeout_sec=5,
    )
    for k, v in overrides.items():
        setattr(ma, k, v)
    return Config(
        cwd=tmp_path,
        model="test",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=ma,
    )


def test_new_worker_id_unique() -> None:
    ids = {new_worker_id() for _ in range(10)}
    assert len(ids) == 10


def test_enqueue_returns_worker_id(tmp_path: Path) -> None:
    parent = Thread(id="p1", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)
    config = _ma_config(tmp_path)
    registry = WorkerRegistry(config, store, emitter=EventEmitter(), parent_thread=parent)

    def fake_run(*args, **kwargs):
        turn = Turn(status="completed")
        turn.items = [UserMessageItem(text="done")]
        return turn

    registry._run_turn_fn = fake_run
    msg, item = registry.enqueue(parent, {"task": "work"}, depth=0)
    assert "queued" in msg.lower() or "Worker" in msg
    assert isinstance(item, CollabWorkerItem)
    assert item.worker_id


def test_wait_workers_collects_summary(tmp_path: Path) -> None:
    from agent.multi_agent.registry import WorkerRecord

    parent = Thread(id="p2", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)
    config = _ma_config(tmp_path)
    registry = WorkerRegistry(config, store, emitter=EventEmitter(), parent_thread=parent)

    wid = new_worker_id()
    record = registry._workers.setdefault(
        wid,
        WorkerRecord(
            worker_id=wid,
            parent_thread_id=parent.id,
            task="t",
            depth=0,
            status="completed",
            summary="all good",
            worker_thread_id="wt-1",
        ),
    )
    record._done.set()

    out = json.loads(registry.wait_workers([wid], timeout_sec=2))
    assert out["workers"][0]["status"] == "completed"
    assert "all good" in out["workers"][0]["summary"]


def test_wait_workers_timeout(tmp_path: Path) -> None:
    parent = Thread(id="p3", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    config = _ma_config(tmp_path)
    registry = WorkerRegistry(config, store, parent_thread=parent)
    wid = new_worker_id()
    from agent.multi_agent.registry import WorkerRecord

    registry._workers[wid] = WorkerRecord(
        worker_id=wid,
        parent_thread_id=parent.id,
        task="slow",
        depth=0,
        status="running",
    )
    out = json.loads(registry.wait_workers([wid], timeout_sec=1))
    assert out["workers"][0]["status"] == "timed_out"


def test_depth_limit_denied(tmp_path: Path) -> None:
    parent = Thread(id="p4", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    config = _ma_config(tmp_path, max_worker_depth=1)
    registry = WorkerRegistry(config, store, parent_thread=parent)
    msg, item = registry.enqueue(parent, {"task": "too deep"}, depth=1)
    assert item.status == "failed"
    assert "depth" in (item.summary or msg).lower()


def test_list_workers(tmp_path: Path) -> None:
    parent = Thread(id="p5", cwd=str(tmp_path), model="test")
    config = _ma_config(tmp_path)
    registry = WorkerRegistry(config, ThreadStore(base_dir=tmp_path / "t"), parent_thread=parent)
    registry.enqueue(parent, {"task": "a", "worker_id": "w-a"}, depth=0)
    data = json.loads(registry.list_workers())
    assert len(data["workers"]) == 1
    assert data["workers"][0]["worker_id"] == "w-a"


def test_queue_respects_max_concurrent(tmp_path: Path) -> None:
    parent = Thread(id="p6", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    config = _ma_config(tmp_path, max_concurrent_workers=2)
    registry = WorkerRegistry(config, store, parent_thread=parent)
    started = threading.Event()
    release = threading.Event()

    def blocking_run(*args, **kwargs):
        started.set()
        release.wait(timeout=5)
        return Turn(status="completed")

    registry._run_turn_fn = blocking_run
    for i in range(4):
        registry.enqueue(parent, {"task": f"t{i}"}, depth=0)
    time.sleep(0.2)
    assert registry._running <= 2
    release.set()
    time.sleep(0.3)


def test_spawn_worker_loop_wait_workers(tmp_path: Path) -> None:
    from agent.loop import run_turn

    parent = Thread(id="parent-wait-12345678", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)
    config = _ma_config(tmp_path, max_concurrent_workers=2)

    tool_calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "spawn_worker", "arguments": '{"task":"w1","worker_id":"wa"}'},
        },
        {
            "id": "c2",
            "type": "function",
            "function": {"name": "wait_workers", "arguments": '{"worker_ids":["wa"]}'},
        },
    ]
    stream_results = [
        CompletionResult("", tool_calls, "tool_calls", None),
        CompletionResult("Done.", [], "stop", None),
    ]

    def fast_worker(self, parent_thread, turn_id, worker_id):
        rec = self._workers[worker_id]
        rec.status = "completed"
        rec.summary = "worker done"
        rec.worker_thread_id = "wt-" + worker_id
        rec._done.set()
        with self._lock:
            self._running = max(0, self._running - 1)

    with patch("agent.loop.OpenRouterClient") as mock_client_cls, patch.object(
        WorkerRegistry, "_run_worker", fast_worker
    ):
        mock_client_cls.return_value.stream_completion.side_effect = stream_results
        run_turn(parent, "parallel", config, store, events=EventEmitter())

    loaded = store.load_thread(parent.id)
    assert any(i.type == "collabWorker" for t in loaded.turns for i in t.items)


def test_worker_failure_structured(tmp_path: Path) -> None:
    parent = Thread(id="pfail", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    config = _ma_config(tmp_path)
    registry = WorkerRegistry(config, store, parent_thread=parent)

    def boom(*args, **kwargs):
        raise RuntimeError("worker exploded")

    registry._run_turn_fn = boom
    registry.enqueue(parent, {"task": "fail"}, depth=0)
    time.sleep(0.5)
    rec = next(iter(registry._workers.values()))
    assert rec.status == "failed"
    assert "exploded" in (rec.summary or "")


def test_fifo_queue_order(tmp_path: Path) -> None:
    parent = Thread(id="pfifo", cwd=str(tmp_path), model="test")
    config = _ma_config(tmp_path, max_concurrent_workers=1)
    registry = WorkerRegistry(
        config, ThreadStore(base_dir=tmp_path / "t"), parent_thread=parent
    )
    order: list[str] = []

    def track_run(self, parent_thread, turn_id, worker_id):
        order.append(worker_id)
        rec = self._workers[worker_id]
        rec.status = "completed"
        rec._done.set()
        with self._lock:
            self._running = max(0, self._running - 1)
        self._pump_queue(parent_thread, turn_id)

    with patch.object(WorkerRegistry, "_run_worker", track_run):
        registry.enqueue(parent, {"task": "1", "worker_id": "w1"}, depth=0)
        registry.enqueue(parent, {"task": "2", "worker_id": "w2"}, depth=0)
        time.sleep(0.3)
    assert order[0] == "w1"
