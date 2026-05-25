from __future__ import annotations

from typing import Callable

from agent.config import Config
from agent.execution.base import ExecutionBackend
from agent.execution.docker import DockerExecutionBackend
from agent.execution.local import LocalExecutionBackend


def get_execution_backend(
    config: Config,
    *,
    backend_override: str | None = None,
    docker_image: str | None = None,
    docker_runner=None,
) -> ExecutionBackend:
    backend = backend_override or config.execution.backend
    if backend == "docker":
        return DockerExecutionBackend(
            config,
            image=docker_image or config.execution.docker_image_override,
            runner=docker_runner,
        )
    return LocalExecutionBackend(config)


def backend_display(config: Config) -> str:
    if config.execution.backend == "docker":
        img = config.execution.docker_image_override or config.execution.default_image
        return f"docker ({img})"
    return "local (host)"
