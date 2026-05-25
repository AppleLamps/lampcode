from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.execution.base import ExecutionResult
from agent.execution.mount import build_volume_mount, container_workdir
from agent.sandbox.classifier import CommandRisk, classify_command
from agent.sandbox.policy import SandboxMode
from tools.files import truncate_output


def build_docker_run_argv(
    config: Config,
    host_cwd: Path,
    cmd: str,
    *,
    workdir: str | None = None,
    read_only_mount: bool = False,
    image: str | None = None,
) -> list[str]:
    exe = config.execution.docker.binary
    image = image or config.execution.default_image
    mount = build_volume_mount(
        host_cwd,
        config.execution.workspace_mount,
        read_only=read_only_mount,
    )
    container_wd = container_workdir(config.execution.workspace_mount, workdir)

    argv = [
        exe,
        "run",
        "--rm",
        "-v",
        mount,
        "-w",
        container_wd,
        f"--memory={config.execution.memory_limit}",
        f"--cpus={config.execution.cpu_limit}",
    ]

    if config.execution.docker.platform:
        argv.extend(["--platform", config.execution.docker.platform])

    network = config.execution.network
    argv.extend(["--network", network])

    argv.extend([image, "/bin/sh", "-lc", cmd])
    return argv


def docker_backend_allowed(config: Config, cmd: str) -> tuple[bool, str]:
    if config.sandbox_mode == SandboxMode.READ_ONLY:
        risk = classify_command(cmd)
        if risk not in (CommandRisk.READ, CommandRisk.TEST):
            return False, "docker backend denied in read-only sandbox for non-read commands"
    return True, ""


class DockerExecutionBackend:
    name = "docker"

    def __init__(
        self,
        config: Config,
        *,
        image: str | None = None,
        runner: Callable[[list[str], int], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._config = config
        self._image = image
        self._runner = runner

    def run(
        self,
        cwd: Path,
        cmd: str,
        *,
        workdir: str | None = None,
        timeout: int = 120,
        max_output: int = 20_000,
    ) -> ExecutionResult:
        from agent.telemetry import trace_span

        with trace_span("execution.docker.run", backend="docker"):
            return self._execute_run(
                cwd, cmd, workdir=workdir, timeout=timeout, max_output=max_output
            )

    def _execute_run(
        self,
        cwd: Path,
        cmd: str,
        *,
        workdir: str | None = None,
        timeout: int = 120,
        max_output: int = 20_000,
    ) -> ExecutionResult:
        allowed, reason = docker_backend_allowed(self._config, cmd)
        if not allowed:
            return ExecutionResult(
                output=f"Docker execution blocked: {reason}",
                exit_code=-1,
                duration_ms=0,
                backend="docker",
                meta={"blocked": True, "reason": reason},
            )

        read_only = self._config.sandbox_mode == SandboxMode.READ_ONLY
        image = self._image or self._config.execution.default_image
        argv = build_docker_run_argv(
            self._config,
            cwd,
            cmd,
            workdir=workdir,
            read_only_mount=read_only,
            image=image,
        )

        start = time.monotonic()
        container_id: str | None = None
        try:
            if self._runner:
                proc = self._runner(argv, timeout)
            else:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            duration_ms = int((time.monotonic() - start) * 1000)
            output = (proc.stdout or "") + (proc.stderr or "")
            output = truncate_output(output, max_output)
            exit_code = proc.returncode if proc.returncode is not None else -1
            if exit_code != 0:
                output = f"Exit code: {exit_code}\n{output}"
            return ExecutionResult(
                output=output,
                exit_code=exit_code,
                duration_ms=duration_ms,
                backend="docker",
                meta={
                    "image": image,
                    "container_id": container_id,
                    "network": self._config.execution.network,
                    "read_only_mount": read_only,
                    "argv": argv,
                },
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error: docker command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="docker",
                meta={"image": image, "container_id": container_id},
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error running docker: {exc}",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="docker",
                meta={"image": image},
            )


def check_docker_available(binary: str = "docker") -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [binary, "info"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return True, "daemon reachable"
        return False, proc.stderr.strip() or "docker info failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def check_docker_hello_world(
    binary: str = "docker",
    *,
    skip: bool = False,
) -> tuple[bool, str]:
    if skip:
        return True, "skipped (AGENT_SKIP_DOCKER_INTEGRATION=1)"
    try:
        proc = subprocess.run(
            [binary, "run", "--rm", "hello-world"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            return True, "hello-world ok"
        return False, proc.stderr.strip() or "hello-world failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
