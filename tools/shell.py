from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from agent.paths import resolve_path_within_cwd
from tools.files import truncate_output


def run_command(
    cwd: Path,
    cmd: str,
    workdir: str | None = None,
    timeout: int = 120,
    max_output: int = 20_000,
) -> tuple[str, int, int]:
    """Run a shell command. Returns (output, exit_code, duration_ms)."""
    base = cwd
    if workdir:
        base = resolve_path_within_cwd(cwd, workdir)

    start = time.monotonic()
    try:
        # shell=True needed for pipes, redirects, and built-ins
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
        return output, proc.returncode or 0, duration_ms
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return f"Error: command timed out after {timeout}s", -1, duration_ms
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return f"Error running command: {exc}", -1, duration_ms
