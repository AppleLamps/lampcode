from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.config import Config
from agent.execution.ssh import (
    SshExecutionBackend,
    build_remote_shell_command,
    build_ssh_argv,
    build_ssh_target,
    check_ssh_available,
    expand_ssh_path,
    validate_ssh_config,
)
from agent.settings import SshExecutionSettings, SshJumpSettings


def _config(**kwargs) -> Config:
    defaults = dict(cwd=Path("."), model="test", openrouter_api_key="x")
    defaults.update(kwargs)
    cfg = Config(**defaults)
    cfg.execution.backend = "ssh"
    cfg.execution.ssh = SshExecutionSettings(
        host="devbox.local",
        user="ubuntu",
        port=22,
        identity_file="~/.ssh/id_ed25519",
        known_hosts="~/.ssh/known_hosts",
        remote_workspace="/home/ubuntu/workspace",
    )
    return cfg


def test_expand_ssh_path() -> None:
    expanded = expand_ssh_path("~/keys/id")
    assert expanded.endswith("keys/id") or "id" in expanded


def test_build_ssh_target() -> None:
    settings = SshExecutionSettings(host="h", user="u")
    assert build_ssh_target(settings) == "u@h"


def test_build_remote_shell_command() -> None:
    settings = SshExecutionSettings(remote_workspace="/home/ubuntu/workspace")
    cmd = build_remote_shell_command(settings, "pytest -q", workdir="src")
    assert "/home/ubuntu/workspace/src" in cmd
    assert "pytest -q" in cmd


def test_build_ssh_argv_basic() -> None:
    cfg = _config()
    argv = build_ssh_argv(cfg, "echo hi")
    assert argv[0] == "ssh"
    assert "-i" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "ubuntu@devbox.local" in argv
    assert argv[-1] == "echo hi" or "echo hi" in argv[-1]


def test_build_ssh_argv_custom_port() -> None:
    cfg = _config()
    cfg.execution.ssh.port = 2222
    argv = build_ssh_argv(cfg, "uname -a")
    idx = argv.index("-p")
    assert argv[idx + 1] == "2222"


def test_build_ssh_argv_jump_host() -> None:
    cfg = _config()
    cfg.execution.ssh.jump = SshJumpSettings(host="bastion.example.com", user="jump")
    argv = build_ssh_argv(cfg, "ls")
    assert "-J" in argv
    assert "jump@bastion.example.com" in argv


def test_validate_ssh_config_missing_host() -> None:
    ok, msg = validate_ssh_config(SshExecutionSettings(user="u", remote_workspace="/w"))
    assert not ok
    assert "host" in msg


def test_validate_ssh_config_missing_user() -> None:
    ok, msg = validate_ssh_config(
        SshExecutionSettings(host="h", remote_workspace="/w", strict_host_key_checking=False)
    )
    assert not ok


def test_validate_ssh_config_strict_known_hosts(tmp_path: Path) -> None:
    kh = tmp_path / "known_hosts"
    kh.write_text("host key")
    ident = tmp_path / "id"
    ident.write_text("key")
    settings = SshExecutionSettings(
        host="h",
        user="u",
        remote_workspace="/w",
        known_hosts=str(kh),
        identity_file=str(ident),
    )
    ok, msg = validate_ssh_config(settings)
    assert ok, msg


def test_ssh_backend_mocked_run() -> None:
    cfg = _config()

    def fake_runner(argv, timeout):
        proc = MagicMock()
        proc.stdout = "Linux\n"
        proc.stderr = ""
        proc.returncode = 0
        return proc

    backend = SshExecutionBackend(cfg, runner=fake_runner)
    result = backend.run(Path("."), "uname -a")
    assert result.backend == "ssh"
    assert result.exit_code == 0
    assert "Linux" in result.output
    assert result.meta["remote_host"] == "devbox.local"


def test_ssh_backend_invalid_config() -> None:
    cfg = _config()
    cfg.execution.ssh.host = ""
    backend = SshExecutionBackend(cfg)
    result = backend.run(Path("."), "echo")
    assert result.exit_code == -1
    assert "invalid" in result.output.lower()


def test_ssh_backend_timeout() -> None:
    cfg = _config()

    def slow(argv, timeout):
        raise __import__("subprocess").TimeoutExpired(argv, timeout)

    backend = SshExecutionBackend(cfg, runner=slow)
    result = backend.run(Path("."), "sleep 99", timeout=1)
    assert result.exit_code == -1
    assert "timed out" in result.output.lower()


def test_check_ssh_available() -> None:
    ok, msg = check_ssh_available()
    # OpenSSH on Windows typically available; message non-empty either way
    assert isinstance(ok, bool)
    assert msg


def test_factory_ssh_backend() -> None:
    from agent.execution.factory import get_execution_backend

    cfg = _config()
    backend = get_execution_backend(cfg)
    assert backend.name == "ssh"
