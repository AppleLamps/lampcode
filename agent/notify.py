from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from agent.settings import NotifySettings


def fire_notify(
    settings: NotifySettings,
    *,
    thread_id: str,
    turn_id: str | None,
    status: str,
    cwd: Path,
) -> None:
    if not settings.command.strip():
        return
    env = os.environ.copy()
    env["AGENT_THREAD_ID"] = thread_id
    env["AGENT_TURN_ID"] = turn_id or ""
    env["AGENT_STATUS"] = status
    env["AGENT_CWD"] = str(cwd)

    def _run() -> None:
        try:
            subprocess.run(
                settings.command,
                shell=True,
                env=env,
                timeout=settings.timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    threading.Thread(target=_run, daemon=True).start()
