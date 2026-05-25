from pathlib import Path

import pytest

from agent.config import Config
from agent.execution.sync.planner import (
    DEFAULT_EXCLUDES,
    build_scp_remote_target,
    build_remote_spec,
    estimate_sync_size,
    plan_sync,
    select_transport,
    _should_exclude,
)
from agent.execution.sync.rsync import build_rsync_argv
from agent.execution.sync.scp import build_scp_push_argv, build_scp_pull_argv
from agent.settings import SshExecutionSettings, SshSyncSettings


def _cfg(tmp_path: Path) -> Config:
    c = Config(cwd=tmp_path, model="t", openrouter_api_key="x")
    c.execution.backend = "ssh"
    c.execution.ssh = SshExecutionSettings(
        host="devbox",
        user="ubuntu",
        remote_workspace="/home/ubuntu/workspace",
        sync_enabled=True,
        sync=SshSyncSettings(max_upload_mb=200, fetch_remote_manifest=False),
    )
    return c


def test_should_exclude_pycache() -> None:
    assert _should_exclude("src/__pycache__/x.pyc", DEFAULT_EXCLUDES, False)


def test_should_exclude_dotfiles_by_default() -> None:
    assert _should_exclude(".env", DEFAULT_EXCLUDES, False)


def test_should_include_dotfiles_when_enabled() -> None:
    assert not _should_exclude(".env", DEFAULT_EXCLUDES, True)


def test_estimate_sync_size(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "b.pyc").write_bytes(b"x" * 100)
    b, n = estimate_sync_size(tmp_path, excludes=DEFAULT_EXCLUDES)
    assert n == 1
    assert b == 5


def test_plan_sync_size_guard(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (201 * 1024 * 1024))
    cfg = _cfg(tmp_path)
    plan, err = plan_sync(cfg, "push")
    assert plan is None
    assert err and "exceeds limit" in err


def test_plan_sync_force(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (201 * 1024 * 1024))
    cfg = _cfg(tmp_path)
    plan, err = plan_sync(cfg, "push", force=True)
    assert plan is not None
    assert err is None


def test_build_scp_push_argv(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    argv = build_scp_push_argv(cfg, tmp_path, excludes=[])
    assert argv[0] == "scp"
    assert "-r" in argv
    assert "ubuntu@devbox" in argv[-1]


def test_build_scp_pull_argv(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    argv = build_scp_pull_argv(cfg, tmp_path)
    assert argv[0] == "scp"
    assert str(tmp_path) in argv[-1]


def test_build_rsync_push_argv(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    argv = build_rsync_argv(cfg, tmp_path, direction="push", excludes=["node_modules"])
    assert argv[0] == "rsync"
    assert "-az" in argv
    assert "--exclude" in argv
    assert "ubuntu@devbox" in " ".join(argv)


def test_build_rsync_pull_argv(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    argv = build_rsync_argv(cfg, tmp_path, direction="pull", excludes=[])
    assert argv[0] == "rsync"
    assert str(tmp_path.resolve()) in argv[-1]


def test_select_transport_auto(monkeypatch) -> None:
    sync = SshSyncSettings(transport="auto")
    monkeypatch.setattr(
        "agent.execution.sync.planner.check_transport_available",
        lambda n: (n == "scp", "scp"),
    )
    assert select_transport(sync) == "scp"


def test_run_sync_push_mocked(tmp_path: Path) -> None:
    from agent.execution.sync.service import run_sync_push

    cfg = _cfg(tmp_path)
    (tmp_path / "f.txt").write_text("x")

    def fake_runner(argv, timeout):
        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return P()

    result, item = run_sync_push(cfg, force=True, runner=fake_runner)
    assert result.ok
    assert item.status == "completed"
    assert item.direction == "push"


def test_run_sync_push_emits_events(tmp_path: Path) -> None:
    from agent.events import EventEmitter
    from agent.execution.sync.service import run_sync_push

    cfg = _cfg(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    events: list[str] = []
    emitter = EventEmitter(lambda e: events.append(e.type))

    def fake_runner(argv, timeout):
        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    run_sync_push(cfg, force=True, emitter=emitter, thread_id="t", turn_id="u", runner=fake_runner)
    assert "execution.sync.started" in events
    assert "execution.sync.completed" in events


def test_sync_status(tmp_path: Path) -> None:
    from agent.execution.sync.service import sync_status

    cfg = _cfg(tmp_path)
    st = sync_status(cfg)
    assert st["sync_enabled"] is True
    assert "bytes_estimated" in st


def test_build_remote_spec(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    spec = build_remote_spec(cfg)
    assert "ubuntu@devbox" in spec


def test_build_scp_remote_target(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert build_scp_remote_target(cfg) == "ubuntu@devbox:/home/ubuntu/workspace"


def test_plan_sync_disabled(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.execution.ssh.sync_enabled = False
    plan, err = plan_sync(cfg, "push")
    assert plan is None


def test_sync_pull_mocked(tmp_path: Path) -> None:
    from agent.execution.sync.service import run_sync_pull

    cfg = _cfg(tmp_path)

    def fake_runner(argv, timeout):
        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    result, item = run_sync_pull(cfg, runner=fake_runner)
    assert result.ok
    assert item.direction == "pull"
