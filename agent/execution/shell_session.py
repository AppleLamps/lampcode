from __future__ import annotations

import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.settings import ShellSettings


@dataclass
class ShellSessionResult:
    output: str
    exit_code: int
    duration_ms: int
    session_id: str
    meta: dict[str, Any] = field(default_factory=dict)


class ShellSession:
    """Persistent shell session (best-effort; falls back to one-shot when PTY unavailable)."""

    def __init__(
        self,
        session_id: str,
        cwd: Path,
        settings: ShellSettings,
    ) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.settings = settings
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._last_active = time.monotonic()
        self._pty_available = self._probe_pty()

    @staticmethod
    def _probe_pty() -> bool:
        if platform.system() == "Windows":
            return False
        try:
            import pty  # noqa: F401

            return True
        except ImportError:
            return False

    def _ensure_process(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        shell = "powershell.exe" if platform.system() == "Windows" else "/bin/sh"
        self._proc = subprocess.Popen(
            [shell],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def run(self, cmd: str, *, stdin: str | None = None, timeout: int = 120) -> ShellSessionResult:
        with self._lock:
            self._last_active = time.monotonic()
            if not self.settings.persistent or not self.settings.enabled:
                return self._run_oneshot(cmd, timeout=timeout)
            if not self._pty_available:
                return self._run_oneshot(cmd, timeout=timeout, stdin=stdin)
            return self._run_persistent(cmd, stdin=stdin, timeout=timeout)

    def _run_oneshot(
        self,
        cmd: str,
        *,
        timeout: int,
        stdin: str | None = None,
    ) -> ShellSessionResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=stdin,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
        except subprocess.TimeoutExpired:
            output = f"Command timed out after {timeout}s"
            code = 124
        duration = int((time.monotonic() - start) * 1000)
        return ShellSessionResult(
            output=output,
            exit_code=code,
            duration_ms=duration,
            session_id=self.session_id,
            meta={"persistent": False, "pty": False},
        )

    def _run_persistent(
        self,
        cmd: str,
        *,
        stdin: str | None,
        timeout: int,
    ) -> ShellSessionResult:
        start = time.monotonic()
        self._ensure_process()
        assert self._proc and self._proc.stdin and self._proc.stdout
        payload = cmd if not stdin else f"{stdin}\n{cmd}"
        self._proc.stdin.write(payload + "\n")
        self._proc.stdin.flush()
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
            if len(lines) > 500:
                break
        duration = int((time.monotonic() - start) * 1000)
        return ShellSessionResult(
            output="\n".join(lines),
            exit_code=0,
            duration_ms=duration,
            session_id=self.session_id,
            meta={"persistent": True, "pty": self._pty_available},
        )

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    def idle_expired(self) -> bool:
        return (time.monotonic() - self._last_active) > self.settings.idle_timeout_sec


class ShellSessionManager:
    _global: ShellSessionManager | None = None

    def __init__(self) -> None:
        self._sessions: dict[str, ShellSession] = {}

    @classmethod
    def global_manager(cls) -> ShellSessionManager:
        if cls._global is None:
            cls._global = cls()
        return cls._global

    def get(
        self,
        thread_id: str,
        cwd: Path,
        settings: ShellSettings,
        *,
        new_session: bool = False,
        session_id: str | None = None,
    ) -> ShellSession:
        key = session_id or thread_id
        if new_session and key in self._sessions:
            self._sessions[key].close()
            del self._sessions[key]
        if key not in self._sessions:
            self._sessions[key] = ShellSession(key, cwd, settings)
        else:
            sess = self._sessions[key]
            if sess.idle_expired():
                sess.close()
                self._sessions[key] = ShellSession(key, cwd, settings)
        return self._sessions[key]

    def close_thread(self, thread_id: str) -> None:
        for key in list(self._sessions):
            if key == thread_id or key.startswith(thread_id):
                self._sessions[key].close()
                del self._sessions[key]


def pty_support_status() -> dict[str, Any]:
    system = platform.system()
    if system == "Windows":
        return {
            "available": False,
            "backend": "oneshot",
            "note": "ConPTY persistent shell not enabled; one-shot subprocess fallback",
        }
    try:
        import pty  # noqa: F401

        return {"available": True, "backend": "pty", "note": "Unix PTY available"}
    except ImportError:
        return {"available": False, "backend": "oneshot", "note": "pty module unavailable"}
