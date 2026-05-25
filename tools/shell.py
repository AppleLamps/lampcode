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
) -> tuple[str, int, int, dict | None]:
    """Run a shell command via execution backend. Returns (output, exit_code, duration_ms, meta)."""
    if config is None:
        raise ValueError("config is required for run_command")

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
