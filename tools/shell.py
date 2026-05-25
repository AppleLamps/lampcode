from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from agent.paths import resolve_path_within_cwd
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
    """Run a shell command. Returns (output, exit_code, duration_ms, isolation_meta)."""
    if config and config.use_isolation:
        from agent.isolation.runner import run_isolated_command

        result = run_isolated_command(
            cwd,
            cmd,
            workdir=workdir,
            timeout=timeout,
            max_output=max_output,
            settings=config.isolation,
        )
        meta = {
            "pid": result.pid,
            "cwd": result.cwd,
            "stripped_env_count": result.stripped_env_count,
        }
        return result.output, result.exit_code, result.duration_ms, meta

    base = cwd
    if workdir:
        base = resolve_path_within_cwd(cwd, workdir)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/bash" if sys.platform != "win32" else None,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        output = (proc.stdout or "") + (proc.stderr or "")
        output = truncate_output(output, max_output)
        if proc.returncode != 0:
            output = f"Exit code: {proc.returncode}\n{output}"
        return output, proc.returncode or 0, duration_ms, None
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return f"Error: command timed out after {timeout}s", -1, duration_ms, None
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return f"Error running command: {exc}", -1, duration_ms, None
