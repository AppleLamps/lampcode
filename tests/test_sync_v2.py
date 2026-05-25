from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Config
from agent.execution.sync.conflicts import (
    SyncPlanResult,
    apply_conflict_strategy,
    classify_sync,
    resolve_single_conflict,
)
from agent.execution.sync.incremental import build_incremental_plan, effective_sync_mode, plan_dry_run
from agent.execution.sync.manifest import ManifestEntry, SyncManifest, load_manifest, save_manifest
from agent.execution.sync.planner import _should_exclude
from agent.execution.sync.service import run_sync_push, run_sync_plan
from agent.execution.sync.state import SyncStateStore, ThreadSyncState
from agent.settings import SshExecutionSettings, SshSyncSettings


def _cfg(tmp_path: Path, **sync_kw) -> Config:
    c = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    c.execution.backend = "ssh"
    c.execution.ssh = SshExecutionSettings(
        host="devbox.local",
        user="ubuntu",
        remote_workspace="/home/ubuntu/workspace",
        sync_enabled=True,
        sync=SshSyncSettings(**sync_kw),
    )
    return c


def _write_manifest(tmp_path: Path, files: dict[str, tuple[float, int]]) -> None:
    manifest = SyncManifest(
        generated_at="2020-01-01T00:00:00+00:00",
        files={p: ManifestEntry(mtime=m, size=s) for p, (m, s) in files.items()},
    )
    path = tmp_path / ".agent-cli" / "sync-manifest.json"
    save_manifest(path, manifest)


def test_effective_sync_mode_full_without_manifest(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert effective_sync_mode(cfg, manifest_on_disk=False) == "full"


def test_effective_sync_mode_incremental_with_manifest(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {"a.txt": (1.0, 10)})
    cfg = _cfg(tmp_path)
    assert effective_sync_mode(cfg, manifest_on_disk=True) == "incremental"


def test_classify_push_only_local_changed() -> None:
    local = SyncManifest(files={"a.txt": ManifestEntry(mtime=2.0, size=10)})
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=10)})
    state = ThreadSyncState(thread_id="t")
    plan = classify_sync(local, remote, state)
    assert plan.push == ["a.txt"]
    assert plan.conflict == []


def test_classify_pull_only_remote_changed() -> None:
    local = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=10)})
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=2.0, size=10)})
    state = ThreadSyncState(thread_id="t")
    plan = classify_sync(local, remote, state)
    assert plan.pull == ["a.txt"]


def test_classify_conflict_both_changed() -> None:
    local = SyncManifest(files={"a.txt": ManifestEntry(mtime=3.0, size=10)})
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=4.0, size=12)})
    state = ThreadSyncState(thread_id="t")
    from agent.execution.sync.state import FileSyncState

    state.files["a.txt"] = FileSyncState(
        path="a.txt",
        local_mtime=1.0,
        local_size=10,
        remote_mtime=1.0,
        remote_size=10,
    )
    plan = classify_sync(local, remote, state)
    assert "a.txt" in plan.conflict


def test_classify_unchanged() -> None:
    local = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=10)})
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=10)})
    state = ThreadSyncState(thread_id="t")
    plan = classify_sync(local, remote, state)
    assert plan.unchanged == ["a.txt"]


def test_apply_conflict_local_wins() -> None:
    plan = SyncPlanResult(conflict=["x.txt"])
    updated, resolved, err = apply_conflict_strategy(plan, "local-wins")
    assert err is None
    assert "x.txt" in updated.push
    assert updated.conflict == []
    assert resolved == ["x.txt"]


def test_apply_conflict_abort() -> None:
    plan = SyncPlanResult(conflict=["x.txt"])
    _, _, err = apply_conflict_strategy(plan, "abort")
    assert err is not None


def test_resolve_single_conflict() -> None:
    plan = SyncPlanResult(conflict=["src/foo.py"])
    assert resolve_single_conflict(plan, "src/foo.py", "remote-wins")
    assert "src/foo.py" in plan.pull


def test_plan_dry_run_counts(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    _write_manifest(tmp_path, {"a.txt": (1.0, 5)})
    cfg = _cfg(tmp_path)
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=5)})
    out = plan_dry_run(cfg, remote_manifest=remote)
    assert "counts" in out
    assert out["mode"] in ("full", "incremental")


def test_build_incremental_plan_falls_back_when_no_manifest(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    cfg = _cfg(tmp_path)
    plan, err = build_incremental_plan(cfg, "push", force=True)
    assert plan is None
    assert err is None


def test_build_incremental_plan_with_manifest(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    _write_manifest(tmp_path, {"a.txt": (1.0, 1)})
    cfg = _cfg(tmp_path)
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=1)})
    plan, err = build_incremental_plan(cfg, "push", remote_manifest=remote, force=True)
    assert err is None
    assert plan is not None
    assert plan.mode == "incremental"
    assert "a.txt" in plan.classification.push


def test_incremental_push_mocked_scp(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("data", encoding="utf-8")
    _write_manifest(tmp_path, {"a.txt": (1.0, 1)})
    cfg = _cfg(tmp_path)
    remote = SyncManifest(files={"a.txt": ManifestEntry(mtime=1.0, size=1)})

    def runner(argv, timeout=600):
        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    result, item = run_sync_push(
        cfg,
        force=True,
        thread_id="thread1",
        remote_manifest=remote,
        runner=runner,
    )
    assert result.ok
    assert item.plan is not None
    assert item.plan.get("push", 0) >= 1


def test_sync_state_store_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.execution.sync.state.sync_state_dir",
        lambda: tmp_path / "sync-state",
    )
    store = SyncStateStore()
    state = ThreadSyncState(thread_id="abc")
    store.save(state)
    loaded = store.load("abc")
    assert loaded.thread_id == "abc"


def test_should_exclude_node_modules() -> None:
    assert _should_exclude("node_modules/pkg/index.js", ["node_modules"], False)


def test_run_sync_plan_cli_shape(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    out = run_sync_plan(cfg)
    assert "summary" in out
    assert "mode" in out


def test_max_files_guard(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {f"f{i}.txt": (1.0, 1) for i in range(3)})
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    cfg = _cfg(tmp_path, max_files_per_sync=2)
    remote = SyncManifest(
        files={f"f{i}.txt": ManifestEntry(mtime=2.0, size=2) for i in range(3)}
    )
    plan, err = build_incremental_plan(cfg, "push", remote_manifest=remote, force=True)
    assert plan is None
    assert "max_files_per_sync" in (err or "")


def test_conflict_strategy_remote_wins() -> None:
    plan = SyncPlanResult(conflict=["b.txt"])
    updated, resolved, err = apply_conflict_strategy(plan, "remote-wins")
    assert err is None
    assert "b.txt" in updated.pull


def test_new_local_file_classified_push() -> None:
    local = SyncManifest(files={"new.txt": ManifestEntry(mtime=1.0, size=3)})
    remote = SyncManifest()
    plan = classify_sync(local, remote, ThreadSyncState(thread_id="t"))
    assert "new.txt" in plan.push


def test_new_remote_file_classified_pull() -> None:
    local = SyncManifest()
    remote = SyncManifest(files={"remote.txt": ManifestEntry(mtime=1.0, size=3)})
    plan = classify_sync(local, remote, ThreadSyncState(thread_id="t"))
    assert "remote.txt" in plan.pull


def test_plan_summary_line() -> None:
    plan = SyncPlanResult(push=["a"], pull=["b"], conflict=["c"])
    assert "push 1" in plan.summary_line()
    assert "pull 1" in plan.summary_line()
    assert "conflicts 1" in plan.summary_line()
