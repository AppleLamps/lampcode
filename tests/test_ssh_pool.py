from pathlib import Path

from agent.config import Config
from agent.execution.ssh import build_ssh_argv
from agent.execution.ssh_pool import (
    SshSessionPool,
    append_pool_options,
    build_pool_ssh_options,
    control_socket_path,
    pool_key,
)
from agent.settings import SshExecutionSettings, SshPoolSettings


def _cfg() -> Config:
    c = Config(cwd=Path("."), model="t", openrouter_api_key="x")
    c.execution.ssh = SshExecutionSettings(
        host="devbox",
        user="ubuntu",
        identity_file="/tmp/id",
        pool=SshPoolSettings(enabled=True, idle_timeout_sec=300),
    )
    return c


def test_pool_key() -> None:
    assert pool_key(_cfg()) == ("devbox", "ubuntu", 22, "/tmp/id")


def test_control_socket_path() -> None:
    p = control_socket_path(_cfg())
    assert p.name.startswith("cm-")
    assert "ssh-sockets" in str(p)


def test_build_pool_ssh_options() -> None:
    opts = build_pool_ssh_options(_cfg())
    assert "ControlMaster=auto" in opts
    assert any("ControlPath=" in o for o in opts)


def test_append_pool_options() -> None:
    argv = append_pool_options(["ssh", "-p", "22", "user@host", "echo hi"], _cfg())
    assert "ControlMaster=auto" in argv


def test_append_pool_options_disabled() -> None:
    cfg = _cfg()
    cfg.execution.ssh.pool.enabled = False
    argv = append_pool_options(["ssh", "user@host", "cmd"], cfg)
    assert "ControlMaster=auto" not in argv


def test_ssh_argv_includes_pool() -> None:
    cfg = _cfg()
    argv = build_ssh_argv(cfg, "echo ok")
    assert "ControlMaster=auto" in argv


def test_pool_acquire_release() -> None:
    SshSessionPool.reset_for_tests()
    pool = SshSessionPool.global_pool()
    cfg = _cfg()
    s1 = pool.acquire(cfg)
    assert s1 is not None
    assert s1.in_use
    pool.release(cfg)
    assert not pool._sessions[pool_key(cfg)].in_use


def test_pool_idle_eviction() -> None:
    SshSessionPool.reset_for_tests()
    pool = SshSessionPool.global_pool()
    cfg = _cfg()
    cfg.execution.ssh.pool.idle_timeout_sec = 0
    pool.acquire(cfg)
    pool.release(cfg)
    pool._evict_idle(0)
    assert pool_key(cfg) not in pool._sessions
