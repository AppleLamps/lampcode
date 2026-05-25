from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agent.config import Config
from agent.metrics import MetricsCollector
from agent.models import Thread, new_id
from agent.multi_agent.dag_state import DagStateStore, PersistedDagState, clear_dag_state_for_thread
from agent.multi_agent.registry import WorkerRegistry
from agent.settings import MultiAgentSettings
from agent.store import ThreadStore


def _thread(store: ThreadStore, tmp_path: Path, thread_id: str | None = None) -> Thread:
    t = Thread(id=thread_id or new_id(), cwd=str(tmp_path), model="m")
    store.create_thread(t)
    return t


@pytest.fixture
def dag_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "dag-state"
    monkeypatch.setattr("agent.multi_agent.dag_state.default_dag_state_dir", lambda: d)
    return d


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    yield
    MetricsCollector.reset_for_tests()


def _cfg(tmp_path: Path, **ma_kw) -> Config:
    c = Config(cwd=tmp_path, model="m", openrouter_api_key="x", multi_agent=True)
    defaults = dict(
        enabled=True,
        dag_enabled=True,
        dag_persist_across_turns=True,
        dag_max_age_sec=86400,
        checkpoint_enabled=True,
    )
    defaults.update(ma_kw)
    c.multi_agent = MultiAgentSettings(**defaults)
    return c


def test_dag_state_save_load(dag_dir: Path) -> None:
    store = DagStateStore(dag_dir)
    state = PersistedDagState(
        thread_id="t1",
        last_turn_id="turn1",
        nodes=[{"worker_id": "w1", "status": "completed", "task": "a"}],
        edges=[{"from": "w0", "to": "w1"}],
        dag_status="running",
        spawn_count=1,
    )
    store.save(state)
    loaded = store.load("t1")
    assert loaded is not None
    assert loaded.last_turn_id == "turn1"


def test_dag_state_clear(dag_dir: Path) -> None:
    store = DagStateStore(dag_dir)
    store.save(PersistedDagState(thread_id="t1", last_turn_id="x"))
    assert store.clear("t1") is True
    assert store.load("t1") is None


def test_dag_state_expired() -> None:
    store = DagStateStore()
    state = PersistedDagState(thread_id="t1", last_turn_id="x", updated_at=time.time() - 100000)
    assert store.is_expired(state, 3600) is True


def test_save_from_registry(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    reg = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-a")
    reg.enqueue(thread, {"task": "task one"}, turn_id="turn-a")
    path = DagStateStore(dag_dir).save_from_registry(reg, thread_id=thread.id, turn_id="turn-a")
    assert path is not None
    loaded = DagStateStore(dag_dir).load(thread.id)
    assert loaded is not None
    assert len(loaded.nodes) >= 1


def test_registry_loads_persisted_turn2(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    reg1 = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-1")
    _, c1 = reg1.enqueue(thread, {"task": "first", "worker_id": "w1"}, turn_id="turn-1")
    reg1._workers[c1.worker_id].status = "completed"
    reg1._workers[c1.worker_id]._done.set()
    reg1.enqueue(thread, {"task": "second", "worker_id": "w2", "depends_on": ["w1"]}, turn_id="turn-1")
    DagStateStore(dag_dir).save_from_registry(reg1, thread_id=thread.id, turn_id="turn-1")

    reg2 = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-2")
    assert "w1" in reg2._workers
    assert reg2._workers["w1"].status == "completed"


def test_persist_disabled_skips_load(tmp_path: Path, dag_dir: Path) -> None:
    DagStateStore(dag_dir).save(
        PersistedDagState(
            thread_id="t-x",
            last_turn_id="a",
            nodes=[
                {
                    "worker_id": "w1",
                    "status": "queued",
                    "task": "t",
                    "depth": 0,
                    "parent_thread_id": "t-x",
                    "worker_dependencies": [],
                }
            ],
        )
    )
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path, thread_id="t-x")
    cfg = _cfg(tmp_path, dag_persist_across_turns=False)
    reg = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-2")
    assert not reg._workers


def test_expired_state_not_loaded(tmp_path: Path, dag_dir: Path) -> None:
    ds = DagStateStore(dag_dir)
    state = PersistedDagState(
        thread_id="t-exp",
        last_turn_id="a",
        updated_at=time.time() - 99999,
        nodes=[
            {
                "worker_id": "w1",
                "status": "queued",
                "task": "t",
                "depth": 0,
                "parent_thread_id": "t-exp",
                "worker_dependencies": [],
            }
        ],
    )
    ds.save(state, touch=False)
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path, thread_id="t-exp")
    cfg = _cfg(tmp_path, dag_max_age_sec=60)
    reg = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-2")
    assert not reg._workers
    assert ds.load("t-exp") is None


def test_delete_thread_clears_dag_state(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore(base_dir=tmp_path / "threads")
    thread = _thread(thread_store, tmp_path)
    DagStateStore(dag_dir).save(PersistedDagState(thread_id=thread.id, last_turn_id="x"))
    clear_dag_state_for_thread(thread.id, base_dir=dag_dir)
    assert DagStateStore(dag_dir).load(thread.id) is None


def test_metrics_on_persist(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    reg = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="t1")
    reg.enqueue(thread, {"task": "job"}, turn_id="t1")
    reg._save_checkpoint(thread, "t1")
    prom = MetricsCollector.global_collector().to_prometheus()
    assert "agent_dag_persist_total" in prom


def test_cycle_still_rejected(tmp_path: Path) -> None:
    from agent.multi_agent.dag import DagEdge

    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    reg = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="t1")
    reg.enqueue(thread, {"task": "a", "worker_id": "a"}, turn_id="t1")
    reg.enqueue(thread, {"task": "b", "worker_id": "b", "depends_on": ["a"]}, turn_id="t1")
    reg.enqueue(thread, {"task": "c", "worker_id": "c", "depends_on": ["b"]}, turn_id="t1")
    reg._workers["a"].worker_dependencies = ["c"]
    reg._edges.append(DagEdge("c", "a"))
    msg, collab = reg.enqueue(thread, {"task": "d", "worker_id": "d", "depends_on": ["a"]}, turn_id="t1")
    assert "cycle" in msg.lower() or collab.status == "failed"


def test_dag_status_json_roundtrip(dag_dir: Path) -> None:
    store = DagStateStore(dag_dir)
    state = PersistedDagState(
        thread_id="abc",
        last_turn_id="turn9",
        dag_status="completed",
        pending_queue=["w3"],
    )
    store.save(state)
    raw = json.loads((dag_dir / "abc.json").read_text(encoding="utf-8"))
    assert raw["dag_status"] == "completed"


def test_get_worker_graph_includes_persisted(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    DagStateStore(dag_dir).save(
        PersistedDagState(
            thread_id=thread.id,
            last_turn_id="turn-1",
            nodes=[
                {
                    "worker_id": "w-old",
                    "status": "completed",
                    "task": "old task",
                    "depth": 0,
                    "parent_thread_id": thread.id,
                    "worker_dependencies": [],
                }
            ],
        )
    )
    reg = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-2")
    graph = json.loads(reg.get_worker_graph())
    ids = {n["worker_id"] for n in graph["nodes"]}
    assert "w-old" in ids


def test_blocked_worker_after_persist(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    reg1 = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-1")
    reg1.enqueue(thread, {"task": "dep", "worker_id": "w1"}, turn_id="turn-1")
    _, c2 = reg1.enqueue(
        thread, {"task": "blocked", "worker_id": "w2", "depends_on": ["w1"]}, turn_id="turn-1"
    )
    assert reg1._workers[c2.worker_id].status == "blocked"
    DagStateStore(dag_dir).save_from_registry(reg1, thread_id=thread.id, turn_id="turn-1")
    reg2 = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-2")
    assert "w2" in reg2._workers


def test_dag_persist_default_off() -> None:
    ma = MultiAgentSettings()
    assert ma.dag_persist_across_turns is False


def test_dag_auto_resume_setting() -> None:
    ma = MultiAgentSettings(dag_auto_resume=True)
    assert ma.dag_auto_resume is True


def test_registry_no_parent_skips_load(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    reg = WorkerRegistry(cfg, ThreadStore(), parent_thread=None, turn_id="t")
    assert not reg._workers


def test_clear_dag_state_helper(dag_dir: Path) -> None:
    ds = DagStateStore(dag_dir)
    ds.save(PersistedDagState(thread_id="tid", last_turn_id="x"))
    clear_dag_state_for_thread("tid", base_dir=dag_dir)
    assert ds.load("tid") is None


def test_spawn_count_preserved(tmp_path: Path, dag_dir: Path) -> None:
    thread_store = ThreadStore()
    thread = _thread(thread_store, tmp_path)
    cfg = _cfg(tmp_path)
    reg1 = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-1")
    reg1.spawn_count = 2
    reg1.enqueue(thread, {"task": "a"}, turn_id="turn-1")
    DagStateStore(dag_dir).save_from_registry(reg1, thread_id=thread.id, turn_id="turn-1")
    reg2 = WorkerRegistry(cfg, thread_store, parent_thread=thread, turn_id="turn-2")
    assert reg2.spawn_count >= 2
