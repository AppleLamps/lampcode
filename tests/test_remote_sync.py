from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.config import Config
from agent.execution.sync.manifest import ManifestEntry, SyncManifest, save_manifest
from agent.execution.sync.remote import (
    build_fetch_manifest_command,
    fetch_remote_manifest,
    load_remote_state_snapshot,
    remote_manifest_full_path,
    replicate_remote_state_if_enabled,
    resolve_remote_manifest,
    save_remote_state_snapshot,
    scan_remote_index,
)
from agent.execution.sync.service import format_plan_verbose, run_sync_fetch_remote, run_sync_plan
from agent.settings import SshExecutionSettings, SshSyncSettings


@dataclass
class MockProc:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


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


def test_remote_manifest_full_path() -> None:
    cfg = _cfg(Path("."))
    assert remote_manifest_full_path(cfg).endswith(".agent-cli/sync-manifest.json")


def test_build_fetch_manifest_command() -> None:
    cfg = _cfg(Path("."))
    cmd = build_fetch_manifest_command(cfg)
    assert "cat" in cmd
    assert "sync-manifest.json" in cmd


def test_fetch_remote_manifest_success() -> None:
    cfg = _cfg(Path("."))
    manifest = SyncManifest(
        files={"a.txt": ManifestEntry(mtime=1.0, size=10)},
    )

    def runner(argv, timeout=0):
        return MockProc(stdout=json.dumps(manifest.to_dict()))

    result = fetch_remote_manifest(cfg, runner=runner)
    assert result.ok
    assert not result.missing
    assert "a.txt" in result.manifest.files


def test_fetch_remote_manifest_missing_create() -> None:
    cfg = _cfg(Path("."), on_remote_manifest_missing="create")

    def runner(argv, timeout=0):
        return MockProc(returncode=1, stderr="No such file or directory")

    result = fetch_remote_manifest(cfg, runner=runner)
    assert result.ok
    assert result.missing


def test_fetch_remote_manifest_missing_abort() -> None:
    cfg = _cfg(Path("."), on_remote_manifest_missing="abort")

    def runner(argv, timeout=0):
        return MockProc(returncode=1, stderr="No such file")

    result = fetch_remote_manifest(cfg, runner=runner)
    assert not result.ok
    assert result.missing


def test_fetch_remote_manifest_invalid_json() -> None:
    cfg = _cfg(Path("."))

    def runner(argv, timeout=0):
        return MockProc(stdout="not-json")

    result = fetch_remote_manifest(cfg, runner=runner)
    assert not result.ok
    assert "invalid" in (result.error or "").lower()


def test_fetch_remote_manifest_disabled() -> None:
    cfg = _cfg(Path("."), fetch_remote_manifest=False)

    def runner(argv, timeout=0):
        raise AssertionError("should not run ssh when disabled")

    result = fetch_remote_manifest(cfg, runner=runner)
    assert result.ok
    assert result.missing


def test_scan_remote_index_parses_lines() -> None:
    cfg = _cfg(Path("."))

    def runner(argv, timeout=0):
        return MockProc(stdout="src/a.py|100|42\nsrc/b.py|200|99\n")

    manifest, err = scan_remote_index(cfg, runner=runner)
    assert err is None
    assert manifest.files["src/a.py"].size == 42
    assert manifest.files["src/b.py"].mtime == 200.0


def test_scan_remote_index_failure() -> None:
    cfg = _cfg(Path("."))

    def runner(argv, timeout=0):
        return MockProc(returncode=1, stderr="scan failed")

    manifest, err = scan_remote_index(cfg, runner=runner)
    assert err
    assert not manifest.files


def test_resolve_remote_manifest_fetches_when_none(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    remote = SyncManifest(files={"x.txt": ManifestEntry(mtime=1.0, size=5)})

    def runner(argv, timeout=0):
        return MockProc(stdout=json.dumps(remote.to_dict()))

    got, err = resolve_remote_manifest(cfg, runner=runner)
    assert err is None
    assert got is not None
    assert "x.txt" in got.files


def test_resolve_remote_manifest_scans_when_empty(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    calls = {"n": 0}

    def runner(argv, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return MockProc(returncode=1, stderr="No such file")
        return MockProc(stdout="only.txt|1|2\n")

    got, err = resolve_remote_manifest(cfg, runner=runner)
    assert err is None
    assert got is not None
    assert "only.txt" in got.files


def test_resolve_remote_manifest_passes_through() -> None:
    cfg = _cfg(Path("."))
    provided = SyncManifest(files={"p.txt": ManifestEntry(mtime=1.0, size=1)})
    got, err = resolve_remote_manifest(cfg, remote_manifest=provided)
    assert err is None
    assert got is provided


def test_run_sync_fetch_remote(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, replicate_remote_state=True)
    manifest = SyncManifest(files={"f.txt": ManifestEntry(mtime=1.0, size=1)})

    def runner(argv, timeout=0):
        return MockProc(stdout=json.dumps(manifest.to_dict()))

    out = run_sync_fetch_remote(cfg, thread_id="thread-a", runner=runner)
    assert out["ok"]
    assert out["files"] == 1
    snap = load_remote_state_snapshot("thread-a")
    assert snap is not None
    assert "f.txt" in snap.files


def test_replicate_remote_state_snapshot(tmp_path: Path, monkeypatch) -> None:
    from agent.execution.sync import remote as remote_mod

    state_dir = tmp_path / "sync-state"
    monkeypatch.setattr(remote_mod, "remote_state_snapshot_path", lambda tid: state_dir / f"{tid}.remote.json")
    cfg = _cfg(tmp_path, replicate_remote_state=True)
    manifest = SyncManifest(files={"z.txt": ManifestEntry(mtime=2.0, size=3)})
    replicate_remote_state_if_enabled(cfg, "t1", manifest)
    path = state_dir / "t1.remote.json"
    assert path.exists()


def test_run_sync_plan_with_mocked_remote(tmp_path: Path) -> None:
    (tmp_path / "local.txt").write_text("local", encoding="utf-8")
    cfg = _cfg(tmp_path)
    remote = SyncManifest(files={"remote.txt": ManifestEntry(mtime=1.0, size=5)})

    def runner(argv, timeout=0):
        return MockProc(stdout=json.dumps(remote.to_dict()))

    plan = run_sync_plan(cfg, thread_id="t1", runner=runner)
    assert "error" not in plan
    assert plan["counts"]["push"] >= 1
    assert "remote.txt" in plan["pull"]


def test_run_sync_plan_conflicts(tmp_path: Path) -> None:
    (tmp_path / "both.txt").write_text("local", encoding="utf-8")
    _write_local_manifest(tmp_path, {"both.txt": (2.0, 6)})
    cfg = _cfg(tmp_path)
    remote = SyncManifest(files={"both.txt": ManifestEntry(mtime=3.0, size=7)})
    from agent.execution.sync.state import FileSyncState, SyncStateStore

    store = SyncStateStore()
    state = store.load("t1")
    state.files["both.txt"] = FileSyncState(
        path="both.txt",
        local_mtime=1.0,
        local_size=5,
        remote_mtime=1.0,
        remote_size=5,
    )
    store.save(state)

    def runner(argv, timeout=0):
        return MockProc(stdout=json.dumps(remote.to_dict()))

    plan = run_sync_plan(cfg, thread_id="t1", runner=runner)
    assert "both.txt" in plan.get("conflicts", [])


def test_format_plan_verbose() -> None:
    plan = {
        "mode": "incremental",
        "transport": "scp",
        "summary": "ok",
        "counts": {"push": 1, "pull": 0, "conflict": 0},
        "push": ["a.txt"],
        "pull": [],
        "conflict": [],
    }
    text = format_plan_verbose(plan)
    assert "push:" in text
    assert "a.txt" in text


def _write_local_manifest(tmp_path: Path, files: dict[str, tuple[float, int]]) -> None:
    manifest = SyncManifest(
        files={p: ManifestEntry(mtime=m, size=s) for p, (m, s) in files.items()},
    )
    path = tmp_path / ".agent-cli" / "sync-manifest.json"
    save_manifest(path, manifest)
