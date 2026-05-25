import json
from pathlib import Path

from agent.config import Config
from agent.multi_agent.checkpoint import (
    CheckpointStore,
    SupervisorCheckpoint,
    WorkerCheckpoint,
    restore_registry,
    snapshot_registry,
)
from agent.multi_agent.registry import WorkerRecord, WorkerRegistry
from agent.settings import MultiAgentSettings


def test_checkpoint_save_load(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "cp")
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="u1",
        spawn_count=2,
        workers=[
            WorkerCheckpoint(
                worker_id="w1",
                parent_thread_id="t1",
                task="do thing",
                depth=0,
                status="completed",
                summary="done",
            )
        ],
        status="running",
    )
    path = store.save(cp)
    loaded = store.load("t1", "u1")
    assert loaded is not None
    assert loaded.spawn_count == 2
    assert loaded.workers[0].worker_id == "w1"
    assert path.is_file()


def test_find_latest(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "cp")
    store.save(SupervisorCheckpoint(thread_id="t1", turn_id="u1", status="cancelled"))
    store.save(SupervisorCheckpoint(thread_id="t1", turn_id="u2", status="running"))
    latest = store.find_latest("t1")
    assert latest is not None
    assert latest.turn_id in ("u1", "u2")


def test_snapshot_registry(tmp_path: Path) -> None:
    config = Config(
        cwd=tmp_path,
        model="t",
        openrouter_api_key="x",
        multi_agent=MultiAgentSettings(checkpoint_enabled=True),
    )
    from agent.models import Thread

    parent = Thread(id="t1", cwd=str(tmp_path), model="t")
    registry = WorkerRegistry(config, __import__("agent.store", fromlist=["ThreadStore"]).ThreadStore(base_dir=tmp_path / "s"))
    registry._workers["w1"] = WorkerRecord(
        worker_id="w1",
        parent_thread_id="t1",
        task="x",
        depth=0,
        status="completed",
        summary="ok",
    )
    registry._workers["w1"]._done.set()
    cp = snapshot_registry(registry, thread_id="t1", turn_id="u1", spawn_count=1)
    assert len(cp.workers) == 1
    assert cp.workers[0].status == "completed"


def test_restore_skips_completed(tmp_path: Path) -> None:
    config = Config(
        cwd=tmp_path,
        model="t",
        openrouter_api_key="x",
        multi_agent=MultiAgentSettings(enabled=True),
    )
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="u1",
        workers=[
            WorkerCheckpoint(
                worker_id="w1",
                parent_thread_id="t1",
                task="a",
                depth=0,
                status="completed",
            ),
            WorkerCheckpoint(
                worker_id="w2",
                parent_thread_id="t1",
                task="b",
                depth=0,
                status="failed",
            ),
        ],
    )
    from agent.store import ThreadStore

    reg = restore_registry(config, ThreadStore(base_dir=tmp_path / "s"), cp, retry_failed=False)
    assert reg._workers["w1"].status == "completed"
    assert "w2" not in reg._pending_queue


def test_restore_retry_failed(tmp_path: Path) -> None:
    config = Config(
        cwd=tmp_path,
        model="t",
        openrouter_api_key="x",
        multi_agent=MultiAgentSettings(enabled=True),
    )
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="u1",
        workers=[
            WorkerCheckpoint(
                worker_id="w2",
                parent_thread_id="t1",
                task="b",
                depth=0,
                status="failed",
            ),
        ],
    )
    from agent.store import ThreadStore

    reg = restore_registry(config, ThreadStore(base_dir=tmp_path / "s"), cp, retry_failed=True)
    assert "w2" in reg._pending_queue
    assert reg._workers["w2"].status == "queued"


def test_checkpoint_roundtrip_json(tmp_path: Path) -> None:
    cp = SupervisorCheckpoint(
        thread_id="t",
        turn_id="u",
        messages=[{"role": "user", "content": "hi"}],
    )
    data = cp.to_dict()
    restored = SupervisorCheckpoint.from_dict(data)
    assert restored.messages[0]["content"] == "hi"


def test_list_for_thread(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "cp")
    store.save(SupervisorCheckpoint(thread_id="t1", turn_id="a"))
    store.save(SupervisorCheckpoint(thread_id="t1", turn_id="b"))
    assert len(store.list_for_thread("t1")) == 2


def test_save_checkpoint_from_registry(tmp_path: Path) -> None:
    from agent.multi_agent.checkpoint import save_checkpoint_from_registry
    from agent.models import Thread

    config = Config(
        cwd=tmp_path,
        model="t",
        openrouter_api_key="x",
        multi_agent=MultiAgentSettings(
            checkpoint_enabled=True,
            checkpoint_dir=str(tmp_path / "cp"),
        ),
    )
    from agent.store import ThreadStore

    parent = Thread(id="t1", cwd=str(tmp_path), model="t")
    reg = WorkerRegistry(config, ThreadStore(base_dir=tmp_path / "s"), parent_thread=parent)
    reg.spawn_count = 1
    path = save_checkpoint_from_registry(
        reg, config, thread_id="t1", turn_id="u1", spawn_count=1
    )
    assert path is not None
    assert path.is_file()
