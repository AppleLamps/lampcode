from __future__ import annotations

import time
from pathlib import Path

from agent.config import Config
from agent.execution.sync.base import SyncResult
from agent.execution.sync.planner import build_scp_remote_target, subprocess_runner
from agent.execution.ssh_util import expand_ssh_path


def build_scp_push_argv(
    config: Config,
    local_cwd: Path,
    *,
    excludes: list[str],
) -> list[str]:
    ssh = config.execution.ssh
    argv = ["scp", "-r"]
    if ssh.port != 22:
        argv.extend(["-P", str(ssh.port)])
    if ssh.identity_file:
        argv.extend(["-i", expand_ssh_path(ssh.identity_file)])
    if ssh.strict_host_key_checking and ssh.known_hosts:
        argv.extend(["-o", f"UserKnownHostsFile={expand_ssh_path(ssh.known_hosts)}"])
    argv.append(str(local_cwd) + "/")
    argv.append(build_scp_remote_target(config) + "/")
    return argv


def build_scp_pull_argv(config: Config, local_cwd: Path) -> list[str]:
    ssh = config.execution.ssh
    argv = ["scp", "-r"]
    if ssh.port != 22:
        argv.extend(["-P", str(ssh.port)])
    if ssh.identity_file:
        argv.extend(["-i", expand_ssh_path(ssh.identity_file)])
    if ssh.strict_host_key_checking and ssh.known_hosts:
        argv.extend(["-o", f"UserKnownHostsFile={expand_ssh_path(ssh.known_hosts)}"])
    argv.append(build_scp_remote_target(config) + "/")
    argv.append(str(local_cwd) + "/")
    return argv


class ScpTransport:
    name = "scp"

    def __init__(self, config: Config, *, runner=None) -> None:
        self._config = config
        self._runner = runner or subprocess_runner

    def push(
        self,
        local_cwd: Path,
        remote_spec: str,
        *,
        excludes: list[str],
        delete_remote_extra: bool,
        runner=None,
    ) -> SyncResult:
        run = runner or self._runner
        argv = build_scp_push_argv(self._config, local_cwd, excludes=excludes)
        start = time.monotonic()
        try:
            proc = run(argv, timeout=600)
            duration_ms = int((time.monotonic() - start) * 1000)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "scp failed").strip()
                return SyncResult(
                    ok=False,
                    direction="push",
                    transport="scp",
                    duration_ms=duration_ms,
                    summary=err,
                    error=err,
                    argv=argv,
                )
            return SyncResult(
                ok=True,
                direction="push",
                transport="scp",
                duration_ms=duration_ms,
                summary="scp push completed",
                argv=argv,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return SyncResult(
                ok=False,
                direction="push",
                transport="scp",
                duration_ms=duration_ms,
                summary=str(exc),
                error=str(exc),
                argv=argv,
            )

    def pull(
        self,
        local_cwd: Path,
        remote_spec: str,
        *,
        excludes: list[str],
        runner=None,
    ) -> SyncResult:
        run = runner or self._runner
        argv = build_scp_pull_argv(self._config, local_cwd)
        start = time.monotonic()
        try:
            proc = run(argv, timeout=600)
            duration_ms = int((time.monotonic() - start) * 1000)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "scp pull failed").strip()
                return SyncResult(
                    ok=False,
                    direction="pull",
                    transport="scp",
                    duration_ms=duration_ms,
                    summary=err,
                    error=err,
                    argv=argv,
                )
            return SyncResult(
                ok=True,
                direction="pull",
                transport="scp",
                duration_ms=duration_ms,
                summary="scp pull completed",
                argv=argv,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return SyncResult(
                ok=False,
                direction="pull",
                transport="scp",
                duration_ms=duration_ms,
                summary=str(exc),
                error=str(exc),
                argv=argv,
            )
