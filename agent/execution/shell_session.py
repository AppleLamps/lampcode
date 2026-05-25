from __future__ import annotations

import platform
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.sandbox.retry import truncate_shell_output
from agent.settings import ShellSettings

ShellBackend = Literal["oneshot", "pipes", "pty"]


@dataclass
class ShellSessionResult:
    output: str
    exit_code: int
    duration_ms: int
    session_id: str
    meta: dict[str, Any] = field(default_factory=dict)


class ShellSession:
    """Persistent shell session (pipe-based; Unix PTY when available)."""

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
        self._backend = self._resolve_backend(settings)

    @staticmethod
    def _probe_pty() -> bool:
        if platform.system() == "Windows":
            return False
        try:
            import pty  # noqa: F401

            return True
        except ImportError:
            return False

    @staticmethod
    def _resolve_backend(settings: ShellSettings) -> ShellBackend:
        if not settings.enabled or not settings.persistent:
            return "oneshot"
        if platform.system() != "Windows" and settings.pty and ShellSession._probe_pty():
            return "pty"
        return "pipes"

    def _shell_argv(self) -> list[str]:
        if platform.system() == "Windows":
            return ["cmd.exe", "/Q"]
        return ["/bin/sh"]

    def _ensure_process(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            self._shell_argv(),
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def run(
        self,
        cmd: str,
        *,
        stdin: str | None = None,
        timeout: int = 120,
        max_output_chars: int | None = None,
        yield_ms: int | None = None,
    ) -> ShellSessionResult:
        with self._lock:
            self._last_active = time.monotonic()
            cap = max_output_chars if max_output_chars is not None else self.settings.max_output_chars
            wait_sec = (yield_ms if yield_ms is not None else self.settings.default_yield_ms) / 1000
            run_timeout = min(timeout, max(1, int(wait_sec))) if self._backend != "oneshot" else timeout
            if self._backend == "oneshot":
                result = self._run_oneshot(cmd, timeout=timeout, stdin=stdin)
            else:
                result = self._run_persistent(cmd, stdin=stdin, timeout=run_timeout)
            output, truncated = truncate_shell_output(result.output, cap)
            meta = dict(result.meta)
            meta["truncated"] = truncated
            meta["max_output_chars"] = cap
            if yield_ms is not None:
                meta["yield_ms"] = yield_ms
            return ShellSessionResult(
                output=output,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                session_id=result.session_id,
                meta=meta,
            )

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
            exit_code=code if code is not None else 1,
            duration_ms=duration,
            session_id=self.session_id,
            meta={"persistent": False, "backend": "oneshot", "pty": False},
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
        marker = f"__AGENT_DONE_{uuid.uuid4().hex}__"
        windows = platform.system() == "Windows"
        eol = "\r\n" if windows else "\n"
        script = cmd if not stdin else f"{stdin}{eol}{cmd}"
        if windows:
            payload = f"{script}{eol}echo {marker} %ERRORLEVEL%{eol}"
        else:
            payload = f"{script}{eol}echo {marker}:$?{eol}"
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

        lines: list[str] = []
        exit_code = 0
        deadline = time.monotonic() + timeout
        timed_out = True
        while time.monotonic() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                if self._proc.poll() is not None:
                    break
                continue
            stripped = line.rstrip("\r\n")
            if marker in stripped:
                timed_out = False
                if windows:
                    tail = stripped.split(marker, 1)[-1].strip()
                    if tail.isdigit():
                        exit_code = int(tail)
                elif f"{marker}:" in stripped:
                    tail = stripped.split(f"{marker}:", 1)[-1].strip()
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
        return ShellSessionResult(
            output="\n".join(lines),
            exit_code=exit_code,
            duration_ms=duration,
            session_id=self.session_id,
            meta={
                "persistent": True,
                "backend": self._backend,
                "pty": self._backend == "pty",
            },
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
            "available": True,
            "backend": "persistent-pipes",
            "pty": False,
            "note": "Pipe-based persistent shell on Windows (cmd.exe); ConPTY not enabled",
        }
    if ShellSession._probe_pty():
        return {
            "available": True,
            "backend": "pty",
            "pty": True,
            "note": "Unix PTY available",
        }
    return {
        "available": True,
        "backend": "persistent-pipes",
        "pty": False,
        "note": "Pipe-based persistent shell; pty module unavailable",
    }
