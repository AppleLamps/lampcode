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
        from agent.telemetry import trace_span

        with trace_span("execution.local.run", backend="local"):
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
        base = cwd
        if workdir:
            base = resolve_path_within_cwd(cwd, workdir)

        exec_meta: dict = {"cwd": str(base)}
        exec_cmd = cmd
        use_shell = True
        argv: list[str] | None = None

        if self._config.sandbox_kernel.enabled:
            from agent.sandbox.kernel import apply_kernel_sandbox

            kernel_result = apply_kernel_sandbox(
                self._config,
                cmd=cmd,
                cwd=str(base),
                tool="run_command",
            )
            if kernel_result.applied:
                exec_meta["kernel_backend"] = kernel_result.backend
                exec_meta["isolation_level"] = kernel_result.isolation_level or "kernel"
                exec_meta.update(kernel_result.meta or {})
                if kernel_result.argv:
                    argv = kernel_result.argv
                    use_shell = False
                elif kernel_result.wrapped_cmd:
                    exec_cmd = kernel_result.wrapped_cmd
            elif kernel_result.reason:
                exec_meta["kernel_fallback"] = kernel_result.reason

        if self._config.use_isolation:
            from agent.isolation.runner import run_isolated_command

            result = run_isolated_command(
                cwd,
                exec_cmd,
                workdir=workdir,
                timeout=timeout,
                max_output=max_output,
                settings=self._config.isolation,
                argv=argv,
                use_shell=use_shell,
            )
            isolation_level = exec_meta.get("isolation_level", "heuristic")
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
                    "isolation_level": isolation_level,
                    **{k: v for k, v in exec_meta.items() if k != "isolation_level"},
                },
            )

        profile_meta = None
        if self._config.sandbox_profiles.enabled:
            from agent.sandbox.profiles import apply_sandbox_profile

            profile_result = apply_sandbox_profile(
                self._config,
                cmd=exec_cmd,
                cwd=str(base),
            )
            profile_meta = profile_result.meta or {"profile": profile_result.profile}
            if profile_result.applied and not exec_meta.get("isolation_level"):
                exec_meta["isolation_level"] = "profile"

        start = time.monotonic()
        try:
            if argv:
                proc = subprocess.run(
                    argv,
                    cwd=str(base),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            else:
                proc = subprocess.run(
                    exec_cmd,
                    shell=use_shell,
                    cwd=str(base),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    executable="/bin/bash" if sys.platform != "win32" and use_shell else None,
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
                meta={**exec_meta, **(profile_meta or {})},
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error: command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="local",
                meta=exec_meta,
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=f"Error running command: {exc}",
                exit_code=-1,
                duration_ms=duration_ms,
                backend="local",
                meta=exec_meta,
            )
