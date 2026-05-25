from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.execution.base import ExecutionResult
from agent.execution.ssh_pool import SshSessionPool, append_pool_options
from agent.execution.ssh_util import expand_ssh_path

__all__ = [
    "SshExecutionBackend",
    "build_remote_shell_command",
    "build_ssh_argv",
    "build_ssh_target",
    "check_ssh_available",
    "expand_ssh_path",
    "validate_ssh_config",
]
from tools.files import truncate_output

from agent.settings import SshExecutionSettings


def validate_ssh_config(settings: SshExecutionSettings) -> tuple[bool, str]:
    if not settings.host:
        return False, "execution.ssh.host is required"
    if not settings.user:
        return False, "execution.ssh.user is required"
    if not settings.remote_workspace:
        return False, "execution.ssh.remote_workspace is required"
    if settings.strict_host_key_checking and settings.known_hosts:
        kh = Path(expand_ssh_path(settings.known_hosts))
        if not kh.is_file():
            return False, f"known_hosts file not found: {kh}"
    elif settings.strict_host_key_checking and not settings.known_hosts:
        return False, "strict_host_key_checking requires execution.ssh.known_hosts"
    if settings.identity_file:
        ident = Path(expand_ssh_path(settings.identity_file))
        if not ident.is_file():
            return False, f"identity file not found: {ident}"
    return True, "ok"


def build_ssh_target(settings: SshExecutionSettings) -> str:
    return f"{settings.user}@{settings.host}"


def build_remote_shell_command(
    settings: SshExecutionSettings,
    cmd: str,
    *,
    workdir: str | None = None,
) -> str:
    base = settings.remote_workspace.rstrip("/")
    if workdir:
        wd = f"{base}/{workdir.lstrip('/')}"
    else:
        wd = base
    inner = f"cd {shlex.quote(wd)} && {cmd}"
    return inner


def build_ssh_argv(
    config: Config,
    remote_cmd: str,
    *,
    binary: str = "ssh",
) -> list[str]:
    settings = config.execution.ssh
    argv = [binary]

    if settings.port != 22:
        argv.extend(["-p", str(settings.port)])

    if settings.identity_file:
        argv.extend(["-i", expand_ssh_path(settings.identity_file)])

    argv.extend(["-o", f"ConnectTimeout={settings.connect_timeout_sec}"])

    if settings.strict_host_key_checking and settings.known_hosts:
        argv.extend(
            [
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={expand_ssh_path(settings.known_hosts)}",
            ]
        )
    else:
        argv.extend(["-o", "StrictHostKeyChecking=no"])

    if settings.jump and settings.jump.host:
        jump = settings.jump
        jump_target = f"{jump.user}@{jump.host}" if jump.user else jump.host
        argv.extend(["-J", jump_target])

    argv.append(build_ssh_target(settings))
    argv.append(remote_cmd)
    return append_pool_options(argv, config)


def check_ssh_available(binary: str = "ssh") -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [binary, "-V"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 or proc.stderr or proc.stdout:
            version = (proc.stderr or proc.stdout or "").strip().splitlines()[0]
            return True, version or "available"
        return False, "ssh -V failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


class SshExecutionBackend:
    name = "ssh"

    def __init__(
        self,
        config: Config,
        *,
        runner: Callable[[list[str], int], subprocess.CompletedProcess[str]] | None = None,
        binary: str = "ssh",
    ) -> None:
        self._config = config
        self._runner = runner
        self._binary = binary

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

        with trace_span("execution.ssh.run", backend="ssh"):
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
        settings = self._config.execution.ssh
        ok, reason = validate_ssh_config(settings)
        if not ok:
            return ExecutionResult(
                output=f"SSH config invalid: {reason}",
                exit_code=-1,
                duration_ms=0,
                backend="ssh",
                meta={"error": reason},
            )

        remote_cmd = build_remote_shell_command(settings, cmd, workdir=workdir)
        argv = build_ssh_argv(self._config, remote_cmd, binary=self._binary)
        timeout = timeout or settings.command_timeout_sec

        pool = SshSessionPool.global_pool()
        pooled = pool.acquire(self._config, emitter=None)
        start = time.monotonic()
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
                backend="ssh",
                meta={
                    "remote_host": settings.host,
                    "remote_user": settings.user,
                    "remote_workspace": settings.remote_workspace,
                    "argv": argv,
                    "pooled": pooled is not None,
                },
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error: ssh command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="ssh",
                meta={
                    "remote_host": settings.host,
                    "remote_user": settings.user,
                },
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error running ssh: {exc}",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="ssh",
                meta={"remote_host": settings.host},
            )
        finally:
            pool.release(self._config)
