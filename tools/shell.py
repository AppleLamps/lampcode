from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from agent.execution.factory import get_execution_backend
from tools.files import truncate_output

if TYPE_CHECKING:
    from agent.config import Config


def run_command(
    cwd: Path,
    cmd: str,
    workdir: str | None = None,
    timeout: int = 120,
    max_output: int = 20_000,
    *,
    config: "Config | None" = None,
    thread_id: str | None = None,
    session_id: str | None = None,
    stdin: str | None = None,
    new_session: bool = False,
) -> tuple[str, int, int, dict | None]:
    """Run a shell command via execution backend. Returns (output, exit_code, duration_ms, meta)."""
    if config is None:
        raise ValueError("config is required for run_command")

    if (
        config.shell.enabled
        and config.execution.backend == "local"
        and thread_id
        and not workdir
    ):
        from agent.execution.shell_session import ShellSessionManager

        mgr = ShellSessionManager.global_manager()
        sess = mgr.get(
            thread_id,
            cwd,
            config.shell,
            new_session=new_session,
            session_id=session_id,
        )
        result = sess.run(cmd, stdin=stdin, timeout=timeout or config.command_timeout)
        output = truncate_output(result.output, max_output)
        meta = dict(result.meta)
        meta["backend"] = "local"
        meta["shell_session"] = result.session_id
        return output, result.exit_code, result.duration_ms, meta

    backend = get_execution_backend(config)
    timeout = timeout or config.execution.command_timeout_sec
    result = backend.run(
        cwd,
        cmd,
        workdir=workdir,
        timeout=timeout,
        max_output=max_output,
    )
    meta = dict(result.meta)
    meta["backend"] = result.backend
    return result.output, result.exit_code, result.duration_ms, meta
