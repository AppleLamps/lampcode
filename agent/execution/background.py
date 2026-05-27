"""Start long-running commands detached from the harness (dev servers, watchers)."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from agent.paths import resolve_path_within_cwd

# Heuristic: Codex-style dev servers and watchers (not tests or one-shot builds).
_SERVER_CMD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"http\.server|https\.server", re.I),
    re.compile(r"\bpython\s+-m\s+http\.server\b", re.I),
    re.compile(r"\b(npx\s+)?http-server\b", re.I),
    re.compile(r"\b(npx\s+)?serve\b", re.I),
    re.compile(r"\buvicorn\b", re.I),
    re.compile(r"\bgunicorn\b", re.I),
    re.compile(r"\bflask\s+run\b", re.I),
    re.compile(r"\brunserver\b", re.I),
    re.compile(r"\bnext\s+dev\b", re.I),
    re.compile(r"\bvite\b(?!\s+build)", re.I),
    re.compile(r"webpack[-\s]dev[-\s]server", re.I),
    re.compile(r"webpack\s+serve\b", re.I),
    re.compile(r"\bnpm\s+run\s+(dev|serve|preview|start)\b", re.I),
    re.compile(r"\bnpm\s+start\b", re.I),
    re.compile(r"\byarn\s+(dev|serve|start)\b", re.I),
    re.compile(r"\bpnpm\s+(dev|serve|start)\b", re.I),
    re.compile(r"\bbun\s+(dev|run)\b", re.I),
    re.compile(r"\bphp\s+-S\b", re.I),
    re.compile(r"\brails\s+s(?:erver)?\b", re.I),
    re.compile(r"\blive-server\b", re.I),
    re.compile(r"\bng\s+serve\b", re.I),
    re.compile(r"\bstreamlit\s+run\b", re.I),
    re.compile(r"\bgradio\b", re.I),
)

_DENY_BACKGROUND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpytest\b", re.I),
    re.compile(r"\bnpm\s+test\b", re.I),
    re.compile(r"\byarn\s+test\b", re.I),
    re.compile(r"\bpnpm\s+test\b", re.I),
    re.compile(r"\bnpm\s+run\s+test\b", re.I),
    re.compile(r"\bcargo\s+test\b", re.I),
    re.compile(r"\bgo\s+test\b", re.I),
    re.compile(r"\bvite\s+build\b", re.I),
    re.compile(r"\bwebpack\b.*\bbuild\b", re.I),
)


def looks_like_long_running_server(cmd: str) -> bool:
    text = cmd.strip()
    if not text:
        return False
    if any(p.search(text) for p in _DENY_BACKGROUND_PATTERNS):
        return False
    return any(p.search(text) for p in _SERVER_CMD_PATTERNS)


def resolve_background(
    cmd: str,
    background: bool | None,
    *,
    auto_background_servers: bool,
) -> bool:
    """True = detached spawn. None + auto → heuristic on cmd."""
    if background is True:
        return True
    if background is False:
        return False
    return auto_background_servers and looks_like_long_running_server(cmd)


def _windows_creation_flags() -> int:
    flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return flags


def start_background_command(
    cwd: Path,
    cmd: str,
    *,
    workdir: str | None = None,
) -> tuple[str, int, int, dict]:
    """Spawn cmd in a new process group and return immediately (does not wait for exit)."""
    base = cwd
    if workdir:
        base = resolve_path_within_cwd(cwd, workdir)

    start = time.monotonic()
    popen_kwargs: dict = {
        "cwd": str(base),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["shell"] = True
        popen_kwargs["args"] = cmd
        flags = _windows_creation_flags()
        if flags:
            popen_kwargs["creationflags"] = flags
    else:
        popen_kwargs["shell"] = True
        popen_kwargs["args"] = cmd
        popen_kwargs["executable"] = "/bin/bash"
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(**popen_kwargs)
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return (
            f"Error starting background command: {exc}",
            -1,
            duration_ms,
            {"background": True, "cwd": str(base)},
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    pid = proc.pid
    output = (
        f"Started background process (pid={pid}).\n"
        f"Command: {cmd}\n"
        f"Working directory: {base}\n"
        "The process keeps running after this tool returns; continue the conversation "
        "and tell the user which URL or port to open."
    )
    meta: dict = {"background": True, "pid": pid, "cwd": str(base)}
    return (
        output,
        0,
        duration_ms,
        meta,
    )
