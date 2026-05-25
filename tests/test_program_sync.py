from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.metrics import MetricsCollector
from agent.multi_agent.program_state import ProgramNode, ProgramState, ProgramStore
from agent.programs.sync.coordinator import ProgramSyncCoordinator, create_backend
from agent.programs.sync.git_backend import GitProgramBackend
from agent.programs.sync.merge import merge_program_states
from agent.programs.sync.s3_backend import S3ProgramBackend
from agent.programs.sync.signing import (
    canonical_json,
    generate_signing_keypair,
    sign_state,
    verify_signed_payload,
)
from agent.settings import CrossThreadGitSyncSettings, CrossThreadS3SyncSettings, CrossThreadSettings


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    from agent.programs.sync.coordinator import reset_coordinator_for_tests

    reset_coordinator_for_tests()
    yield
    MetricsCollector.reset_for_tests()
    reset_coordinator_for_tests()


def _state(pid: str = "prog1", **kwargs) -> ProgramState:
    return ProgramState(program_id=pid, **kwargs)


def test_canonical_json_stable() -> None:
    a = canonical_json({"b": 2, "a": 1})
    b = canonical_json({"a": 1, "b": 2})
    assert a == b


def test_sign_and_verify(tmp_path: Path) -> None:
    generate_signing_keypair("test-key", tmp_path / "keys")
    wrapped = sign_state({"program_id": "p1"}, key_id="test-key", keys_dir=tmp_path / "keys")
    assert verify_signed_payload(wrapped, keys_dir=tmp_path / "keys")


def test_tampered_signature_fails(tmp_path: Path) -> None:
    generate_signing_keypair("test-key", tmp_path / "keys")
    wrapped = sign_state({"program_id": "p1", "nodes": []}, key_id="test-key", keys_dir=tmp_path / "keys")
    wrapped["state"]["program_id"] = "p2"
    assert not verify_signed_payload(wrapped, keys_dir=tmp_path / "keys")


def test_merge_remote_only() -> None:
    remote = _state(nodes=[ProgramNode(worker_id="w1", thread_id="t1")], updated_at=10)
    result = merge_program_states(None, remote)
    assert result.merged is True
    assert result.state.program_id == "prog1"


def test_merge_local_wins_newer() -> None:
    local = _state(updated_at=20)
    remote = _state(updated_at=10, dag_status="done")
    result = merge_program_states(local, remote, last_sync_at=0)
    assert result.state.updated_at == 20


def test_merge_remote_wins_newer() -> None:
    local = _state(updated_at=5, dag_status="running")
    remote = _state(updated_at=15, dag_status="done")
    result = merge_program_states(local, remote, last_sync_at=0)
    assert result.state.dag_status == "done"


def test_merge_conflict_detection() -> None:
    last_sync = 100.0
    local = _state(
        nodes=[ProgramNode(worker_id="w1", thread_id="t1", status="running", updated_at=200)],
        updated_at=200,
    )
    remote = _state(
        nodes=[ProgramNode(worker_id="w1", thread_id="t1", status="failed", updated_at=200)],
        updated_at=200,
    )
    result = merge_program_states(local, remote, last_sync_at=last_sync)
    assert "w1" in result.conflicts


def test_git_backend_push_pull(tmp_path: Path) -> None:
    storage: dict[str, bytes] = {}
    calls: list[str] = []

    def fake_git(*args: str, **kwargs):
        calls.append(" ".join(args))
        return MagicMock(returncode=0, stdout="", stderr="")

    backend = GitProgramBackend(
        CrossThreadGitSyncSettings(repo_path=".agent-cli/program-sync"),
        cwd=tmp_path,
        git_run=fake_git,
    )
    backend.push("abc", b'{"state":{}}')
    assert any("commit" in c for c in calls)
    path = backend._program_path("abc")
    assert path.is_file()


def test_s3_backend_mocked_put_get() -> None:
    storage: dict[str, bytes] = {}

    def put_fn(pid: str, payload: bytes) -> None:
        storage[pid] = payload

    def get_fn(pid: str) -> bytes | None:
        return storage.get(pid)

    backend = S3ProgramBackend(
        CrossThreadS3SyncSettings(bucket="b", prefix="programs/"),
        put_fn=put_fn,
        get_fn=get_fn,
    )
    backend.push("p1", b"data")
    assert backend.pull("p1") == b"data"


def test_s3_list_default_empty() -> None:
    backend = S3ProgramBackend(CrossThreadS3SyncSettings(), list_fn=lambda: [])
    assert backend.list_program_ids() == []


def test_coordinator_push_disabled(tmp_path: Path) -> None:
    from agent.config import Config

    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sync_enabled = False
    store = ProgramStore(tmp_path / "programs")
    coord = ProgramSyncCoordinator(cfg, store=store, backend=None)
    assert coord.push("x")["ok"] is False


def test_coordinator_push_pull_roundtrip(tmp_path: Path, monkeypatch) -> None:
    from agent.config import Config

    storage: dict[str, bytes] = {}
    keys = tmp_path / "keys"
    generate_signing_keypair("program-sync", keys)
    monkeypatch.setattr("agent.programs.sync.signing.default_keys_dir", lambda: keys)

    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    ct = cfg.multi_agent.cross_thread
    ct.sync_enabled = True
    ct.sync_backend = "s3"
    ct.sign_program_state = True
    ct.state_dir = str(tmp_path / "programs")

    store = ProgramStore(tmp_path / "programs")
    state = ProgramState(program_id="sync1", linked_thread_ids=["t1"])
    store.save(state)

    backend = S3ProgramBackend(
        ct.s3,
        put_fn=lambda pid, blob: storage.__setitem__(pid, blob),
        get_fn=lambda pid: storage.get(pid),
    )
    coord = ProgramSyncCoordinator(cfg, store=store, backend=backend)
    assert coord.push("sync1")["ok"] is True

    store2 = ProgramStore(tmp_path / "programs2")
    store2.base_dir.mkdir(parents=True, exist_ok=True)
    coord2 = ProgramSyncCoordinator(cfg, store=store2, backend=backend)
    result = coord2.pull("sync1")
    assert result["ok"] is True
    loaded = store2.load("sync1")
    assert loaded and loaded.linked_thread_ids == ["t1"]


def test_pull_miss(tmp_path: Path) -> None:
    from agent.config import Config

    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sync_enabled = True
    backend = S3ProgramBackend(
        cfg.multi_agent.cross_thread.s3,
        get_fn=lambda _pid: None,
        put_fn=lambda _pid, _blob: None,
    )
    store = ProgramStore(tmp_path / "programs")
    coord = ProgramSyncCoordinator(cfg, store=store, backend=backend)
    assert coord.pull("missing")["updated"] is False


def test_verify_signature_ok(tmp_path: Path, monkeypatch) -> None:
    from agent.config import Config

    generate_signing_keypair("program-sync", tmp_path / "keys")
    monkeypatch.setattr("agent.programs.sync.signing.default_keys_dir", lambda: tmp_path / "keys")
    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sign_program_state = True
    cfg.multi_agent.cross_thread.state_dir = str(tmp_path / "programs")
    store = ProgramStore(tmp_path / "programs")
    store.save(ProgramState(program_id="v1"))
    coord = ProgramSyncCoordinator(cfg, store=store, backend=None)
    result = coord.verify_signature("v1")
    assert result["ok"] is True


def test_schedule_push_debounce(tmp_path: Path) -> None:
    from agent.config import Config

    pushed: list[str] = []
    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sync_enabled = True
    cfg.multi_agent.cross_thread.sync_interval_sec = 60
    cfg.multi_agent.cross_thread.sign_program_state = False
    backend = S3ProgramBackend(
        cfg.multi_agent.cross_thread.s3,
        put_fn=lambda pid, blob: pushed.append(pid),
        get_fn=lambda _pid: None,
    )
    store = ProgramStore(tmp_path / "programs")
    store.save(ProgramState(program_id="d1"))
    coord = ProgramSyncCoordinator(cfg, store=store, backend=backend)
    coord.schedule_push("d1")
    coord.schedule_push("d1")
    assert len(pushed) == 1


def test_pull_if_stale(tmp_path: Path) -> None:
    from agent.config import Config

    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sync_enabled = True
    cfg.multi_agent.cross_thread.sync_interval_sec = 0
    pulled: list[str] = []
    backend = S3ProgramBackend(
        cfg.multi_agent.cross_thread.s3,
        get_fn=lambda pid: (pulled.append(pid) or None),
        put_fn=lambda *_a: None,
    )
    store = ProgramStore(tmp_path / "programs")
    coord = ProgramSyncCoordinator(cfg, store=store, backend=backend)
    coord.pull_if_stale("stale1")
    assert pulled == ["stale1"]


def test_create_backend_git() -> None:
    from agent.config import Config

    cfg = Config.resolve()
    cfg.multi_agent.cross_thread.sync_backend = "git"
    backend = create_backend(cfg)
    assert backend is not None
    assert backend.name == "git"


def test_create_backend_s3() -> None:
    from agent.config import Config

    cfg = Config.resolve()
    cfg.multi_agent.cross_thread.sync_backend = "s3"
    backend = create_backend(cfg)
    assert backend.name == "s3"


def test_create_backend_none() -> None:
    from agent.config import Config

    cfg = Config.resolve()
    cfg.multi_agent.cross_thread.sync_backend = "none"
    assert create_backend(cfg) is None


def test_merge_edges_union() -> None:
    local = _state(edges=[{"from": "a", "to": "b"}])
    remote = _state(edges=[{"from": "b", "to": "c"}], updated_at=999)
    result = merge_program_states(local, remote)
    assert len(result.state.edges) == 2


def test_invalid_signed_pull_raises(tmp_path: Path) -> None:
    from agent.config import Config

    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sync_enabled = True
    cfg.multi_agent.cross_thread.sign_program_state = True
    bad = json.dumps({"state": {"program_id": "x"}, "signature": "bad", "signing_key_id": "k"}).encode()
    backend = S3ProgramBackend(
        cfg.multi_agent.cross_thread.s3,
        get_fn=lambda _pid: bad,
        put_fn=lambda *_a: None,
    )
    store = ProgramStore(tmp_path / "programs")
    coord = ProgramSyncCoordinator(cfg, store=store, backend=backend)
    assert coord.pull("x")["ok"] is False


def test_sync_metrics_on_push(tmp_path: Path) -> None:
    from agent.config import Config

    cfg = Config.resolve(config_path=tmp_path / "missing.toml")
    cfg.multi_agent.cross_thread.sync_enabled = True
    store = ProgramStore(tmp_path / "programs")
    store.save(ProgramState(program_id="m1"))
    backend = S3ProgramBackend(
        cfg.multi_agent.cross_thread.s3,
        put_fn=lambda *_a: None,
        get_fn=lambda _pid: None,
    )
    coord = ProgramSyncCoordinator(cfg, store=store, backend=backend)
    coord.push("m1")
    snap = MetricsCollector.global_collector().snapshot()
    assert snap.labeled_counters.get("agent_program_sync_total", {})
