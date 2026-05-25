from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.events import EventEmitter
from agent.metrics import MetricsCollector
from agent.models import Thread, new_id
from agent.multi_agent.program_state import ProgramNode, ProgramStore, derive_program_id
from agent.multi_agent.registry import WorkerRegistry
from agent.settings import CrossThreadSettings, MultiAgentSettings
from agent.store import ThreadStore


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    yield
    MetricsCollector.reset_for_tests()


def test_derive_program_id_stable(tmp_path: Path) -> None:
    a = derive_program_id(tmp_path, auto=True)
    b = derive_program_id(tmp_path, auto=True)
    assert a == b
    assert len(a) == 16


def test_derive_program_id_mocked_git(tmp_path: Path) -> None:
    with patch("agent.multi_agent.program_state.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "https://github.com/org/repo.git\n"
        pid = derive_program_id(tmp_path, auto=True)
    assert len(pid) == 16


def test_program_store_link_threads(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    state = store.link_thread("prog1", "thread-a", max_threads=5)
    assert "thread-a" in state.linked_thread_ids
    state = store.link_thread("prog1", "thread-b", max_threads=5)
    assert len(state.linked_thread_ids) == 2


def test_program_store_max_threads(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    store.link_thread("p", "t1", max_threads=1)
    with pytest.raises(ValueError, match="max_threads"):
        store.link_thread("p", "t2", max_threads=1)


def test_program_upsert_node(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    node = ProgramNode(worker_id="w-1", thread_id="t1", task="do work", status="queued")
    store.upsert_node("prog", node, thread_id="t1")
    loaded = store.get_node("prog", "w-1")
    assert loaded is not None
    assert loaded.task == "do work"


def test_program_graph_two_threads(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    store.upsert_node(
        "prog",
        ProgramNode(worker_id="w-1", thread_id="t1", status="completed"),
        thread_id="t1",
    )
    store.upsert_node(
        "prog",
        ProgramNode(worker_id="w-2", thread_id="t2", status="running"),
        thread_id="t2",
    )
    graph = store.graph_snapshot("prog")
    assert len(graph["nodes"]) == 2
    assert set(graph["linked_thread_ids"]) >= {"t1", "t2"}


def test_registry_program_scope_sync(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    st = ThreadStore(base_dir=tmp_path / "threads")
    st.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="m",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(
            enabled=True,
            dag_enabled=True,
            cross_thread=CrossThreadSettings(
                enabled=True,
                state_dir=str(tmp_path / "programs"),
            ),
        ),
    )
    events: list = []
    emitter = EventEmitter(lambda e: events.append(e))
    reg = WorkerRegistry(config, st, emitter=emitter, parent_thread=parent, turn_id="turn1")
    reg.enqueue(parent, {"task": "x", "program_scope": True})
    program_id = reg.program_id
    assert program_id
    store = ProgramStore(tmp_path / "programs")
    assert store.get_node(program_id, list(reg._workers.keys())[0]) is not None
    assert any(e.type == "multi_agent.program.linked" for e in events)


def test_wait_workers_cross_thread_lookup(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    st = ThreadStore(base_dir=tmp_path / "threads")
    st.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="m",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(
            enabled=True,
            cross_thread=CrossThreadSettings(enabled=True, state_dir=str(tmp_path / "programs")),
        ),
    )
    reg = WorkerRegistry(config, st, parent_thread=parent)
    program_id = derive_program_id(tmp_path)
    store = ProgramStore(tmp_path / "programs")
    store.upsert_node(
        program_id,
        ProgramNode(worker_id="w-remote", thread_id="other-thread", status="completed", task="remote"),
        thread_id="other-thread",
    )
    reg._program_id = program_id
    out = json.loads(reg.wait_workers(["w-remote"], timeout_sec=1))
    assert out["workers"][0]["status"] == "completed"
    assert out["workers"][0].get("cross_thread") is True


def test_get_worker_graph_includes_program(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    st = ThreadStore(base_dir=tmp_path / "threads")
    st.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="m",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(
            enabled=True,
            cross_thread=CrossThreadSettings(enabled=True, state_dir=str(tmp_path / "programs")),
        ),
    )
    reg = WorkerRegistry(config, st, parent_thread=parent)
    reg.enqueue(parent, {"task": "local", "program_scope": True})
    graph = json.loads(reg.get_worker_graph())
    assert "program_graph" in graph


def test_program_clear(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    store.link_thread("p1", "t1")
    assert store.clear("p1") is True
    assert store.load("p1") is None


def test_program_list(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    store.link_thread("alpha", "t1")
    store.link_thread("beta", "t2")
    ids = store.list_programs()
    assert "alpha" in ids and "beta" in ids


def test_program_metrics_on_node(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    st = ThreadStore(base_dir=tmp_path / "threads")
    st.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="m",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(
            enabled=True,
            cross_thread=CrossThreadSettings(enabled=True, state_dir=str(tmp_path / "programs")),
        ),
    )
    reg = WorkerRegistry(config, st, parent_thread=parent)
    reg.enqueue(parent, {"task": "x", "program_scope": True})
    snap = MetricsCollector.global_collector().snapshot()
    assert snap.labeled_counters["agent_program_dag_nodes_total"].get("queued", 0) >= 1


def test_cross_thread_disabled_no_program_sync(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    st = ThreadStore(base_dir=tmp_path / "threads")
    st.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="m",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(enabled=True, cross_thread=CrossThreadSettings(enabled=False)),
    )
    reg = WorkerRegistry(config, st, parent_thread=parent)
    reg.enqueue(parent, {"task": "x", "program_scope": True})
    assert reg.program_id is None


def test_program_edges_persisted(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    node = ProgramNode(worker_id="w-2", thread_id="t1", status="queued")
    store.upsert_node(
        "p",
        node,
        edges=[{"from": "w-1", "to": "w-2"}],
        thread_id="t1",
    )
    state = store.load("p")
    assert state is not None
    assert state.edges == [{"from": "w-1", "to": "w-2"}]


def test_derive_program_id_no_git(tmp_path: Path) -> None:
    with patch("agent.multi_agent.program_state.subprocess.run", side_effect=OSError("no git")):
        pid = derive_program_id(tmp_path, auto=True)
    assert len(pid) == 16


def test_program_node_update_status(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    store.upsert_node("p", ProgramNode(worker_id="w-1", thread_id="t", status="queued"))
    store.upsert_node("p", ProgramNode(worker_id="w-1", thread_id="t", status="completed"))
    node = store.get_node("p", "w-1")
    assert node.status == "completed"


def test_wait_workers_local_not_found_without_program(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    st = ThreadStore(base_dir=tmp_path / "threads")
    st.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="m",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(enabled=True),
    )
    reg = WorkerRegistry(config, st, parent_thread=parent)
    out = json.loads(reg.wait_workers(["missing"], timeout_sec=1))
    assert out["workers"][0]["status"] == "not_found"


def test_program_graph_empty(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    g = store.graph_snapshot("missing")
    assert g["status"] == "idle"


def test_program_state_roundtrip(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs")
    store.link_thread("p", "t1")
    store.upsert_node("p", ProgramNode(worker_id="w-1", thread_id="t1", task="t"))
    loaded = store.load("p")
    assert loaded.program_id == "p"
    assert loaded.nodes[0].worker_id == "w-1"


def test_auto_program_id_false_uses_cwd_only(tmp_path: Path) -> None:
    pid = derive_program_id(tmp_path, auto=False)
    assert len(pid) == 16
