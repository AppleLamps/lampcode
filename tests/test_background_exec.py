from __future__ import annotations

import sys
import time
from pathlib import Path

from agent.execution.background import (
    looks_like_long_running_server,
    resolve_background,
    start_background_command,
)


def test_looks_like_http_server() -> None:
    assert looks_like_long_running_server("python -m http.server 5173")
    assert looks_like_long_running_server("npm run dev")
    assert not looks_like_long_running_server("pytest -q")
    assert not looks_like_long_running_server("npm test")


def test_resolve_background_auto() -> None:
    assert resolve_background(
        "python -m http.server 8080",
        None,
        auto_background_servers=True,
    )
    assert not resolve_background(
        "python -m http.server 8080",
        None,
        auto_background_servers=False,
    )
    assert resolve_background("echo hi", True, auto_background_servers=False)
    assert not resolve_background("python -m http.server", False, auto_background_servers=True)


def test_start_background_command_returns_immediately(tmp_path: Path) -> None:
    if sys.platform == "win32":
        cmd = f'python -c "import time; time.sleep(60)"'
    else:
        cmd = "sleep 60"
    start = time.monotonic()
    output, exit_code, duration_ms, meta = start_background_command(tmp_path, cmd)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0
    assert exit_code == 0
    assert meta.get("background") is True
    assert meta.get("pid")
    assert "background process" in output.lower()
