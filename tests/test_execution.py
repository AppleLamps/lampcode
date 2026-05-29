from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config import Config
from agent.execution.docker import (
    build_docker_run_argv,
    check_docker_available,
    docker_backend_allowed,
)
from agent.execution.factory import backend_display, get_execution_backend
from agent.execution.mount import build_volume_mount, container_workdir
from agent.execution.base import ExecutionResult
from agent.sandbox.policy import SandboxMode


def _config(**kwargs) -> Config:
    defaults = dict(cwd=Path("."), model="test", openrouter_api_key="x")
    defaults.update(kwargs)
    return Config(**defaults)


def test_build_volume_mount_rw(tmp_path: Path) -> None:
    mount = build_volume_mount(tmp_path, "/workspace", read_only=False)
    assert mount.endswith(":/workspace")
    assert ":ro" not in mount


def test_build_volume_mount_ro(tmp_path: Path) -> None:
    mount = build_volume_mount(tmp_path, "/workspace", read_only=True)
    assert mount.endswith(":/workspace:ro")


def test_container_workdir_nested() -> None:
    assert container_workdir("/workspace", "src/pkg") == "/workspace/src/pkg"


def test_build_docker_run_argv_defaults(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    cfg.execution.backend = "docker"
    argv = build_docker_run_argv(cfg, tmp_path, "echo hi")
    assert argv[0] == "docker"
    assert "run" in argv
    assert "--network" in argv
    idx = argv.index("--network")
    assert argv[idx + 1] == "none"
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--user" in argv
    assert argv[argv.index("--user") + 1] == "65532:65532"
    assert "python:3.12-slim" in argv


def test_build_docker_run_argv_custom_image(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    argv = build_docker_run_argv(cfg, tmp_path, "pytest", image="custom:1")
    assert "custom:1" in argv


def test_build_docker_run_argv_read_only_mount(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    argv = build_docker_run_argv(cfg, tmp_path, "cat x", read_only_mount=True)
    vol_idx = argv.index("-v")
    assert argv[vol_idx + 1].endswith(":ro")


def test_backend_display_local() -> None:
    cfg = _config()
    assert backend_display(cfg) == "local (host)"


def test_backend_display_docker() -> None:
    cfg = _config()
    cfg.execution.backend = "docker"
    assert "docker" in backend_display(cfg)
    assert "python:3.12-slim" in backend_display(cfg)


def test_get_execution_backend_local() -> None:
    cfg = _config()
    backend = get_execution_backend(cfg)
    assert backend.name == "local"


def test_get_execution_backend_docker() -> None:
    cfg = _config()
    cfg.execution.backend = "docker"
    backend = get_execution_backend(cfg)
    assert backend.name == "docker"


def test_docker_backend_denied_read_only_write(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path, sandbox_mode=SandboxMode.READ_ONLY)
    allowed, reason = docker_backend_allowed(cfg, "echo x > out.txt")
    assert not allowed
    assert "read-only" in reason


def test_docker_backend_allowed_read_only_git_status(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path, sandbox_mode=SandboxMode.READ_ONLY)
    allowed, _ = docker_backend_allowed(cfg, "git status")
    assert allowed


def test_docker_execution_mocked(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    cfg.execution.backend = "docker"

    def runner(argv, timeout):
        return MagicMock(returncode=0, stdout="2\n", stderr="")

    from agent.execution.docker import DockerExecutionBackend

    backend = DockerExecutionBackend(cfg, runner=runner)
    result = backend.run(tmp_path, 'python -c "print(1+1)"', timeout=30)
    assert result.exit_code == 0
    assert "2" in result.output
    assert result.backend == "docker"


def test_check_docker_available_mocked() -> None:
    with patch("agent.execution.docker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        ok, msg = check_docker_available("docker")
    assert ok
    assert "reachable" in msg


def test_factory_backend_override() -> None:
    cfg = _config()
    backend = get_execution_backend(cfg, backend_override="docker")
    assert backend.name == "docker"


def test_docker_argv_memory_cpu(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    cfg.execution.memory_limit = "512m"
    cfg.execution.cpu_limit = "2.0"
    argv = build_docker_run_argv(cfg, tmp_path, "echo")
    assert "--memory=512m" in argv
    assert "--cpus=2.0" in argv


def test_registry_docker_run_mocked(tmp_path: Path) -> None:
    from tools.registry import dispatch_tool

    cfg = _config(cwd=tmp_path)
    cfg.execution.backend = "docker"

    mock_result = ExecutionResult(
        output="4",
        exit_code=0,
        duration_ms=10,
        backend="docker",
        meta={"backend": "docker", "image": "python:3.12-slim"},
    )

    with patch("tools.shell.get_execution_backend") as mock_backend:
        instance = MagicMock()
        instance.run.return_value = mock_result
        mock_backend.return_value = instance
        result = dispatch_tool("run_command", {"cmd": "python -c \"print(2+2)\""}, cfg)
    assert result.command_item is not None
    assert result.command_item.backend == "docker"
    assert "4" in result.text


def test_local_execution_metadata_unisolated(tmp_path: Path) -> None:
    from agent.execution.local import LocalExecutionBackend
    from agent.sandbox.policy import SandboxMode

    cfg = _config(cwd=tmp_path, sandbox_mode=SandboxMode.DANGER_FULL_ACCESS)
    cfg.isolation.enabled = False
    cfg.sandbox_kernel.enabled = False
    cfg.sandbox_profiles.enabled = False
    backend = LocalExecutionBackend(cfg)
    result = backend.run(tmp_path, "echo ok", timeout=10)
    assert result.meta["isolated"] is False
    assert result.meta["unisolated_local"] is True
