from __future__ import annotations

import time
from pathlib import Path

from agent.config import Config
from agent.execution.sync.base import SyncResult
from agent.execution.sync.planner import subprocess_runner
from agent.execution.ssh import build_ssh_target
from agent.execution.ssh_util import expand_ssh_path


def build_rsync_argv(
    config: Config,
    local_cwd: Path,
    *,
    direction: str,
    excludes: list[str],
    delete_remote_extra: bool = False,
) -> list[str]:
    ssh = config.execution.ssh
    remote_base = ssh.remote_workspace.rstrip("/") + "/"
    target = f"{build_ssh_target(ssh)}:{remote_base}"
    argv = ["rsync", "-az"]
    if delete_remote_extra and direction == "push":
        argv.append("--delete")
    for ex in excludes:
        argv.extend(["--exclude", ex])
    ssh_parts = []
    if ssh.port != 22:
        ssh_parts.append(f"ssh -p {ssh.port}")
    if ssh.identity_file:
        ident = expand_ssh_path(ssh.identity_file)
        ssh_parts.append(f"ssh -i {ident}")
    if ssh_parts:
        argv.extend(["-e", " ".join(ssh_parts)])
    local = str(local_cwd.resolve()) + "/"
    if direction == "push":
        argv.extend([local, target])
    else:
        argv.extend([target, local])
    return argv


class RsyncTransport:
    name = "rsync"

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
        argv = build_rsync_argv(
            self._config,
            local_cwd,
            direction="push",
            excludes=excludes,
            delete_remote_extra=delete_remote_extra,
        )
        return self._run(argv, "push", run)

    def pull(
        self,
        local_cwd: Path,
        remote_spec: str,
        *,
        excludes: list[str],
        runner=None,
    ) -> SyncResult:
        run = runner or self._runner
        argv = build_rsync_argv(
            self._config,
            local_cwd,
            direction="pull",
            excludes=excludes,
        )
        return self._run(argv, "pull", run)

    def _run(self, argv: list[str], direction: str, run) -> SyncResult:
        start = time.monotonic()
        try:
            proc = run(argv, timeout=600)
            duration_ms = int((time.monotonic() - start) * 1000)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "rsync failed").strip()
                return SyncResult(
                    ok=False,
                    direction=direction,
                    transport="rsync",
                    duration_ms=duration_ms,
                    summary=err,
                    error=err,
                    argv=argv,
                )
            return SyncResult(
                ok=True,
                direction=direction,
                transport="rsync",
                duration_ms=duration_ms,
                summary=f"rsync {direction} completed",
                argv=argv,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return SyncResult(
                ok=False,
                direction=direction,
                transport="rsync",
                duration_ms=duration_ms,
                summary=str(exc),
                error=str(exc),
                argv=argv,
            )
