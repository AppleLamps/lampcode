from __future__ import annotations

import json
from pathlib import Path

from agent.config import Config
from agent.metrics import MetricsCollector
from agent.multi_agent.checkpoint import (
    CheckpointStore,
    SupervisorCheckpoint,
    WorkerCheckpoint,
    compact_checkpoint_history,
    compute_retry_backoff,
    restore_registry,
    snapshot_registry,
)
from agent.multi_agent.registry import WorkerRecord, WorkerRegistry
from agent.settings import MultiAgentSettings
from agent.store import ThreadStore


def test_compute_retry_backoff() -> None:
    assert compute_retry_backoff(5, 1) == 5
    assert compute_retry_backoff(5, 2) == 10
    assert compute_retry_backoff(5, 10) == 60


def test_worker_checkpoint_attempts_roundtrip() -> None:
    wc = WorkerCheckpoint(
        worker_id="w-1",
        parent_thread_id="t1",
        task="lint",
        depth=0,
        status="failed",
        attempts=2,
    )
    data = {"workers": [wc.__dict__]}
    loaded = WorkerCheckpoint(**data["workers"][0])
    assert loaded.attempts == 2


def test_restore_registry_skips_completed() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    store = ThreadStore()
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        workers=[
            WorkerCheckpoint(
                worker_id="w-1",
                parent_thread_id="t1",
                task="done",
                depth=0,
                status="completed",
            )
        ],
    )
    reg = restore_registry(cfg, store, cp, retry_failed=True)
    assert reg._workers["w-1"].status == "completed"
    assert not reg._pending_queue


def test_restore_registry_retries_failed_within_limit() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.multi_agent = MultiAgentSettings(retry_max_attempts=3)
    store = ThreadStore()
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        workers=[
            WorkerCheckpoint(
                worker_id="w-1",
                parent_thread_id="t1",
                task="fix",
                depth=0,
                status="failed",
                attempts=1,
            )
        ],
    )
    reg = restore_registry(cfg, store, cp, retry_failed=True)
    assert reg._workers["w-1"].status == "queued"
    assert reg._workers["w-1"].attempts == 2
    assert "w-1" in reg._pending_queue


def test_restore_registry_stops_retry_at_max() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.multi_agent = MultiAgentSettings(retry_max_attempts=2)
    store = ThreadStore()
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        workers=[
            WorkerCheckpoint(
                worker_id="w-1",
                parent_thread_id="t1",
                task="fix",
                depth=0,
                status="failed",
                attempts=2,
            )
        ],
    )
    reg = restore_registry(cfg, store, cp, retry_failed=True)
    assert reg._workers["w-1"].status == "failed"
    assert not reg._pending_queue


def test_checkpoint_compaction_archives(tmp_path: Path) -> None:
    base = tmp_path / "cp"
    store = CheckpointStore(base)
    cp = SupervisorCheckpoint(thread_id="t1", turn_id="turn1")
    path = store.save(cp)
    compact_checkpoint_history("t1", "turn1", base)
    archive_dir = base / "t1" / "turn1.archive"
    assert archive_dir.is_dir()
    assert any(archive_dir.glob("*.json"))


def test_snapshot_registry_includes_attempts() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    store = ThreadStore()
    reg = WorkerRegistry(cfg, store)
    rec = WorkerRecord(
        worker_id="w-1",
        parent_thread_id="t1",
        task="x",
        depth=0,
        status="failed",
        attempts=2,
    )
    reg._workers["w-1"] = rec
    snap = snapshot_registry(reg, thread_id="t1", turn_id="turn1", spawn_count=1)
    assert snap.workers[0].attempts == 2


def test_metrics_workers_failed_counter() -> None:
    MetricsCollector.reset_for_tests()
    MetricsCollector.global_collector().inc("workers_failed", 3)
    assert MetricsCollector.global_collector().snapshot().counters["workers_failed"] == 3


def test_checkpoint_json_roundtrip(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path)
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        spawn_count=2,
        workers=[
            WorkerCheckpoint(
                worker_id="w-1",
                parent_thread_id="t1",
                task="a",
                depth=0,
                status="running",
                attempts=1,
            )
        ],
    )
    path = store.save(cp)
    loaded = store.load("t1", "turn1")
    assert loaded is not None
    assert loaded.spawn_count == 2
    assert loaded.workers[0].attempts == 1


def test_compact_keeps_latest_checkpoint(tmp_path: Path) -> None:
    base = tmp_path / "cp"
    store = CheckpointStore(base)
    cp = SupervisorCheckpoint(thread_id="t1", turn_id="turn1", status="running")
    main_path = store.save(cp)
    for _ in range(5):
        compact_checkpoint_history("t1", "turn1", base, keep_archives=2)
    assert main_path.is_file()
    archives = list((base / "t1" / "turn1.archive").glob("*.json"))
    assert len(archives) <= 2


def test_prompt_sync_conflict_auto_approve() -> None:
    from approval.gate import prompt_sync_conflict

    assert prompt_sync_conflict("src/a.py", auto_approve=True) == "local-wins"


def test_multi_agent_settings_retry_defaults() -> None:
    ma = MultiAgentSettings()
    assert ma.retry_failed_workers is True
    assert ma.retry_max_attempts == 2
    assert ma.checkpoint_compact_after_workers == 10
