from __future__ import annotations

import platform
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def conpty_available() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import pywinpty  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class ConPtyRunResult:
    output: str
    exit_code: int
    duration_ms: int
    meta: dict[str, Any] = field(default_factory=dict)


class ConPtySession:
    """Windows ConPTY-backed persistent shell session."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._pty: Any = None

    def _ensure_pty(self) -> None:
        if self._pty is not None:
            return
        import pywinpty

        self._pty = pywinpty.PtyProcess.spawn(
            ["cmd.exe", "/Q"],
            cwd=str(self.cwd),
        )

    def run(
        self,
        cmd: str,
        *,
        stdin: str | None = None,
        timeout: int = 120,
    ) -> ConPtyRunResult:
        start = time.monotonic()
        self._ensure_pty()
        marker = f"__AGENT_DONE_{uuid.uuid4().hex}__"
        eol = "\r\n"
        script = cmd if not stdin else f"{stdin}{eol}{cmd}"
        payload = f"{script}{eol}echo {marker} %ERRORLEVEL%{eol}"
        self._pty.write(payload)

        lines: list[str] = []
        exit_code = 0
        deadline = time.monotonic() + timeout
        timed_out = True
        while time.monotonic() < deadline:
            try:
                line = self._pty.readline()
            except EOFError:
                break
            if not line:
                if not self._pty.isalive():
                    break
                time.sleep(0.01)
                continue
            stripped = line.rstrip("\r\n")
            if marker in stripped:
                timed_out = False
                tail = stripped.split(marker, 1)[-1].strip()
                if tail.isdigit():
                    exit_code = int(tail)
                break
            lines.append(stripped)
            if len(lines) > 500:
                timed_out = False
                exit_code = 124
                break

        if timed_out:
            exit_code = 124
            lines.append(f"Command timed out after {timeout}s")

        duration = int((time.monotonic() - start) * 1000)
        return ConPtyRunResult(
            output="\n".join(lines),
            exit_code=exit_code,
            duration_ms=duration,
            meta={"persistent": True, "backend": "conpty", "pty": True},
        )

    def write_stdin(self, data: str) -> None:
        self._ensure_pty()
        payload = data if data.endswith("\r\n") else f"{data}\r\n"
        self._pty.write(payload)

    def close(self) -> None:
        if self._pty is not None:
            try:
                self._pty.close()
            except OSError:
                pass
            self._pty = None
