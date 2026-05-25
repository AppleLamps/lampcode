from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.config import Config
from agent.multi_agent.checkpoint import CheckpointStore, SupervisorCheckpoint, WorkerCheckpoint, restore_registry
from agent.multi_agent.dag import (
    DagEdge,
    aggregate_dependency_summaries,
    deps_satisfied,
    detect_cycle,
    edges_from_dependencies,
    topological_order,
)
from agent.multi_agent.registry import WorkerRegistry
from agent.models import Thread
from agent.settings import MultiAgentSettings
from agent.store import ThreadStore


def _cfg(tmp_path: Path, **ma_kw) -> Config:
    defaults = {
        "enabled": True,
        "dag_enabled": True,
        "max_concurrent_workers": 2,
        "checkpoint_enabled": False,
        "worker_auto_approve": True,
    }
    defaults.update(ma_kw)
    c = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    c.multi_agent = MultiAgentSettings(**defaults)
    return c


def _fast_run_turn(wt, prompt, cfg, st, **kwargs):
    from agent.models import AgentMessageItem, Turn

    turn = Turn(status="completed")
    turn.items.append(AgentMessageItem(text=f"done: {prompt[:30]}"))
    wt.turns.append(turn)
    return turn


def _registry(tmp_path: Path, **ma_kw) -> WorkerRegistry:
    return WorkerRegistry(
        _cfg(tmp_path, **ma_kw),
        ThreadStore(),
        parent_thread=_parent(),
        turn_id="turn1",
        run_turn_fn=_fast_run_turn,
    )


def _parent() -> Thread:
    return Thread(id="t1", cwd=".", model="m")


def test_detect_cycle_direct() -> None:
    edges = [DagEdge("a", "b"), DagEdge("b", "c")]
    cycle = detect_cycle(edges, extra_from="c", extra_to="a")
    assert cycle is not None
    assert "a" in cycle


def test_detect_cycle_none() -> None:
    edges = [DagEdge("a", "b"), DagEdge("a", "c")]
    assert detect_cycle(edges) is None


def test_topological_order_diamond() -> None:
    edges = [
        DagEdge("a", "b"),
        DagEdge("a", "c"),
        DagEdge("b", "d"),
        DagEdge("c", "d"),
    ]
    order = topological_order(["a", "b", "c", "d"], edges)
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_deps_satisfied() -> None:
    deps = {"d": ["a", "b"]}
    statuses = {"a": "completed", "b": "running", "d": "queued"}
    assert not deps_satisfied("d", deps, statuses)
    statuses["b"] = "completed"
    assert deps_satisfied("d", deps, statuses)


def test_edges_from_dependencies() -> None:
    edges = edges_from_dependencies(["b", "c"], {"b": ["a"], "c": ["a"]})
    assert len(edges) == 2


def test_aggregate_dependency_summaries() -> None:
    text = aggregate_dependency_summaries(
        "d",
        {"d": ["a"]},
        {"a": "done task"},
    )
    assert "done task" in text


def test_spawn_rejects_cycle(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    msg, collab = reg.enqueue(_parent(), {"task": "b", "worker_id": "b", "depends_on": ["a"]})
    assert collab.status != "failed"
    msg2, collab2 = reg.enqueue(
        _parent(),
        {"task": "c", "worker_id": "c", "depends_on": ["b"]},
    )
    assert collab2.status != "failed"
    # create cycle a depends on c
    reg._workers["a"].worker_dependencies = ["c"]
    reg._edges.append(DagEdge("c", "a"))
    msg3, collab3 = reg.enqueue(
        _parent(),
        {"task": "d", "worker_id": "d", "depends_on": ["a"]},
    )
    assert "cycle" in msg3.lower() or collab3.status == "failed"


def test_blocked_worker_not_in_pending(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    _, collab = reg.enqueue(
        _parent(),
        {"task": "b", "worker_id": "b", "depends_on": ["a"]},
    )
    assert collab.status == "blocked"
    assert "b" not in reg._pending_queue


def test_batch_register(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    msg, collabs = reg.enqueue_batch(
        _parent(),
        [
            {"task": "a", "worker_id": "a"},
            {"task": "b", "worker_id": "b", "depends_on": ["a"]},
        ],
    )
    assert len(collabs) == 2
    assert "Batch registered" in msg


def test_get_worker_graph(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    data = json.loads(reg.get_worker_graph())
    assert len(data["nodes"]) == 1
    assert data["status"] == "running"


def test_wait_workers_any_mode(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    _, c1 = reg.enqueue(_parent(), {"task": "slow", "worker_id": "slow"})
    reg._workers["slow"].status = "running"
    reg._workers["slow"]._done.clear()
    _, c2 = reg.enqueue(_parent(), {"task": "fast", "worker_id": "fast"})
    reg._workers["fast"].status = "completed"
    reg._workers["fast"]._done.set()
    out = json.loads(reg.wait_workers(["slow", "fast"], timeout_sec=1, mode="any"))
    assert out["mode"] == "any"
    assert any(w["status"] == "completed" for w in out["workers"])


def test_concurrency_cap(tmp_path: Path) -> None:
    reg = _registry(tmp_path, max_concurrent_workers=1)
    reg.enqueue(_parent(), {"task": "w1", "worker_id": "w1"})
    reg.enqueue(_parent(), {"task": "w2", "worker_id": "w2"})
    time.sleep(0.05)
    assert reg._running <= 1


def test_fail_fast_cancels_pending(tmp_path: Path) -> None:
    reg = _registry(tmp_path, dag_fail_fast=True, max_concurrent_workers=1)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    reg.enqueue(_parent(), {"task": "b", "worker_id": "b", "depends_on": ["a"]})
    reg._workers["a"].status = "failed"
    reg._handle_fail_fast("a")
    assert reg._workers["b"].status == "cancelled"


def test_diamond_d_runs_last(tmp_path: Path) -> None:
    reg = _registry(tmp_path, max_concurrent_workers=4)
    reg.enqueue_batch(
        _parent(),
        [
            {"task": "A", "worker_id": "A"},
            {"task": "B", "worker_id": "B", "depends_on": ["A"]},
            {"task": "C", "worker_id": "C", "depends_on": ["A"]},
            {"task": "D", "worker_id": "D", "depends_on": ["B", "C"]},
        ],
    )
    assert reg._workers["D"].status == "blocked"
    reg._workers["A"].status = "completed"
    reg._workers["A"]._done.set()
    reg._promote_blocked_workers()
    assert reg._workers["B"].status in ("queued", "running", "blocked")
    reg._workers["B"].status = "completed"
    reg._workers["B"]._done.set()
    reg._workers["C"].status = "completed"
    reg._workers["C"]._done.set()
    reg._promote_blocked_workers()
    assert reg._workers["D"].status == "queued"


def test_checkpoint_edges_roundtrip(tmp_path: Path) -> None:
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        edges=[{"from": "a", "to": "b"}],
        dag_status="running",
        workers=[
            WorkerCheckpoint(
                worker_id="b",
                parent_thread_id="t1",
                task="t",
                depth=0,
                worker_dependencies=["a"],
            )
        ],
    )
    data = cp.to_dict()
    loaded = SupervisorCheckpoint.from_dict(data)
    assert loaded.edges[0]["from"] == "a"
    assert loaded.dag_status == "running"


def test_restore_registry_dag_ready_only(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    store = ThreadStore()
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        edges=[{"from": "a", "to": "b"}],
        workers=[
            WorkerCheckpoint(
                worker_id="a",
                parent_thread_id="t1",
                task="done",
                depth=0,
                status="completed",
            ),
            WorkerCheckpoint(
                worker_id="b",
                parent_thread_id="t1",
                task="wait",
                depth=0,
                status="queued",
                worker_dependencies=["a"],
            ),
        ],
    )
    reg = restore_registry(cfg, store, cp, parent_thread=None)
    assert reg._workers["b"].status in ("queued", "blocked", "running")


def test_dag_disabled_ignores_deps(tmp_path: Path) -> None:
    reg = _registry(tmp_path, dag_enabled=False)
    _, collab = reg.enqueue(
        _parent(),
        {"task": "x", "worker_id": "x", "depends_on": ["missing"]},
    )
    assert collab.status == "queued"


def test_batch_cycle_rejected(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    msg, collabs = reg.enqueue_batch(
        _parent(),
        [
            {"task": "a", "worker_id": "a", "depends_on": ["b"]},
            {"task": "b", "worker_id": "b", "depends_on": ["a"]},
        ],
    )
    assert not collabs
    assert "cycle" in msg.lower()


def test_wait_workers_deps_mode(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue_batch(
        _parent(),
        [
            {"task": "a", "worker_id": "a"},
            {"task": "b", "worker_id": "b", "depends_on": ["a"]},
        ],
    )
    for wid in ("a", "b"):
        reg._workers[wid].status = "completed"
        reg._workers[wid]._done.set()
    out = json.loads(reg.wait_workers(["b"], mode="deps"))
    ids = {w["worker_id"] for w in out["workers"]}
    assert "a" in ids and "b" in ids


def test_graph_snapshot_edges(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    reg.enqueue(_parent(), {"task": "b", "worker_id": "b", "depends_on": ["a"]})
    snap = reg.build_graph_snapshot()
    assert len(snap.edges) == 1


def test_promote_blocked_to_ready(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    reg.enqueue(_parent(), {"task": "b", "worker_id": "b", "depends_on": ["a"]})
    assert reg._workers["b"].status == "blocked"
    reg._workers["a"].status = "completed"
    reg._promote_blocked_workers()
    assert reg._workers["b"].status == "queued"


def test_missing_dependency_rejected(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    msg, collab = reg.enqueue(
        _parent(),
        {"task": "x", "worker_id": "x", "depends_on": ["nope"]},
    )
    assert collab.status == "failed"
    assert "not found" in msg


def test_self_dependency_rejected(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    msg, collab = reg.enqueue(
        _parent(),
        {"task": "x", "worker_id": "x", "depends_on": ["x"]},
    )
    assert collab.status == "failed"


def test_update_dag_status_completed(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg._workers["a"] = reg._workers.get("a") or __import__(
        "agent.multi_agent.registry", fromlist=["WorkerRecord"]
    ).WorkerRecord("a", "t1", "t", 0, status="completed")
    reg._workers["a"]._done.set()
    reg._update_dag_status()
    assert reg._dag_status in ("completed", "running")


def test_list_workers_includes_deps(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.enqueue(_parent(), {"task": "a", "worker_id": "a"})
    reg.enqueue(_parent(), {"task": "b", "worker_id": "b", "depends_on": ["a"]})
    data = json.loads(reg.list_workers())
    by_id = {w["worker_id"]: w for w in data["workers"]}
    assert by_id["b"]["worker_dependencies"] == ["a"]
