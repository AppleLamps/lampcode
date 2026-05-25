from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from agent.config import Config
from agent.execution.base import ExecutionResult
from agent.paths import resolve_path_within_cwd
from tools.files import truncate_output


class LocalExecutionBackend:
    name = "local"

    def __init__(self, config: Config) -> None:
        self._config = config

    def run(
        self,
        cwd: Path,
        cmd: str,
        *,
        workdir: str | None = None,
        timeout: int = 120,
        max_output: int = 20_000,
    ) -> ExecutionResult:
        if self._config.use_isolation:
            from agent.isolation.runner import run_isolated_command

            result = run_isolated_command(
                cwd,
                cmd,
                workdir=workdir,
                timeout=timeout,
                max_output=max_output,
                settings=self._config.isolation,
            )
            return ExecutionResult(
                output=result.output,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                backend="local",
                meta={
                    "pid": result.pid,
                    "cwd": result.cwd,
                    "stripped_env_count": result.stripped_env_count,
                    "isolated": True,
                },
            )

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
            exit_code = proc.returncode or 0
            if exit_code != 0:
                output = f"Exit code: {exit_code}\n{output}"
            return ExecutionResult(
                output=output,
                exit_code=exit_code,
                duration_ms=duration_ms,
                backend="local",
                meta={"cwd": str(base)},
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error: command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="local",
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error running command: {exc}",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="local",
            )
