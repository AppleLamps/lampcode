from __future__ import annotations

from agent.config import Config
from agent.execution.base import ExecutionBackend
from agent.execution.docker import DockerExecutionBackend
from agent.execution.local import LocalExecutionBackend
from agent.execution.ssh import SshExecutionBackend


def get_execution_backend(
    config: Config,
    *,
    backend_override: str | None = None,
    docker_image: str | None = None,
    docker_runner=None,
    ssh_runner=None,
) -> ExecutionBackend:
    backend = backend_override or config.execution.backend
    if backend == "docker":
        return DockerExecutionBackend(
            config,
            image=docker_image or config.execution.docker_image_override,
            runner=docker_runner,
        )
    if backend == "ssh":
        return SshExecutionBackend(config, runner=ssh_runner)
    return LocalExecutionBackend(config)


def backend_display(config: Config) -> str:
    if config.execution.backend == "docker":
        img = config.execution.docker_image_override or config.execution.default_image
        return f"docker ({img})"
    if config.execution.backend == "ssh":
        ssh = config.execution.ssh
        return f"ssh ({ssh.user}@{ssh.host}:{ssh.remote_workspace})"
    return "local (host)"


def run_execution_test(config: Config, cmd: str, *, workdir: str | None = None) -> dict:
    backend = get_execution_backend(config)
    timeout = config.execution.command_timeout_sec
    result = backend.run(config.cwd, cmd, workdir=workdir, timeout=timeout)
    return {
        "backend": result.backend,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "output": result.output,
        "meta": result.meta,
    }
