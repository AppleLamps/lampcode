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
    yield_ms: int | None = None,
    background: bool | None = None,
) -> tuple[str, int, int, dict | None]:
    """Run a shell command via execution backend. Returns (output, exit_code, duration_ms, meta)."""
    if config is None:
        raise ValueError("config is required for run_command")

    from agent.execution.background import resolve_background, start_background_command

    use_background = resolve_background(
        cmd,
        background,
        auto_background_servers=config.execution.auto_background_servers,
    )
    if use_background:
        if config.execution.backend != "local":
            return (
                "background execution is only supported for local execution backend.",
                -1,
                0,
                {"background": True},
            )
        output, exit_code, duration_ms, meta = start_background_command(
            cwd, cmd, workdir=workdir
        )
        meta["backend"] = "local"
        if background is None:
            meta["background_auto"] = True
        return output, exit_code, duration_ms, meta

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
        result = sess.run(
            cmd,
            stdin=stdin,
            timeout=timeout or config.command_timeout,
            max_output_chars=max_output,
            yield_ms=yield_ms,
        )
        output = result.output
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
