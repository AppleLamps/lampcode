from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent.isolation.env import sanitize_environment
from agent.isolation.paths import enforce_workdir
from agent.settings import IsolationSettings
from tools.files import truncate_output


@dataclass
class IsolationResult:
    output: str
    exit_code: int
    duration_ms: int
    pid: int | None
    cwd: str
    stripped_env_count: int


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def run_isolated_command(
    cwd: Path,
    cmd: str,
    *,
    workdir: str | None = None,
    timeout: int = 120,
    max_output: int = 20_000,
    settings: IsolationSettings,
    source_env: dict[str, str] | None = None,
    kill_tree: Callable[[int], None] | None = None,
    argv: list[str] | None = None,
    use_shell: bool = True,
) -> IsolationResult:
    """Run shell command with env hardening and strict cwd lock."""
    locked_cwd = enforce_workdir(cwd, workdir)
    env, stripped = sanitize_environment(
        source_env,
        allowed_vars=settings.allowed_env_vars,
        strip_env=settings.strip_env,
        clear_network_env_hints=settings.clear_network_env_hints,
    )

    start = time.monotonic()
    kill_fn = kill_tree or kill_process_tree
    popen_kwargs: dict = {
        "cwd": str(locked_cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if argv:
        popen_kwargs["args"] = argv
        popen_kwargs["shell"] = False
    else:
        popen_kwargs["args"] = cmd
        popen_kwargs["shell"] = use_shell
        if sys.platform != "win32":
            popen_kwargs["executable"] = "/bin/bash"
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(**popen_kwargs)
    pid = proc.pid
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        output = (stdout or "") + (stderr or "")
        output = truncate_output(output, max_output)
        exit_code = proc.returncode or 0
        if exit_code != 0:
            output = f"Exit code: {exit_code}\n{output}"
        return IsolationResult(
            output=output,
            exit_code=exit_code,
            duration_ms=duration_ms,
            pid=pid,
            cwd=str(locked_cwd),
            stripped_env_count=stripped,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        if settings.kill_process_tree_on_timeout and pid:
            kill_fn(pid)
        try:
            proc.kill()
        except OSError:
            pass
        return IsolationResult(
            output=f"Error: command timed out after {timeout}s",
            exit_code=-1,
            duration_ms=duration_ms,
            pid=pid,
            cwd=str(locked_cwd),
            stripped_env_count=stripped,
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return IsolationResult(
            output=f"Error running command: {exc}",
            exit_code=-1,
            duration_ms=duration_ms,
            pid=pid,
            cwd=str(locked_cwd),
            stripped_env_count=stripped,
        )
