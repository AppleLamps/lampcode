from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import Config
from agent.execution.ssh_util import expand_ssh_path

_control_unsupported_logged = False


def pool_key(config: Config) -> tuple:
    ssh = config.execution.ssh
    return (
        ssh.host,
        ssh.user,
        ssh.port,
        expand_ssh_path(ssh.identity_file) if ssh.identity_file else "",
    )


def control_socket_path(config: Config) -> Path:
    key = "|".join(str(p) for p in pool_key(config))
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    base = Path.home() / ".agent-cli" / "ssh-sockets"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"cm-{digest}.sock"


def build_pool_ssh_options(config: Config) -> list[str]:
    """Extra OpenSSH options for ControlMaster pooling."""
    pool = config.execution.ssh.pool
    if not pool.enabled:
        return []
    sock = control_socket_path(config)
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={sock}",
        "-o",
        f"ControlPersist={pool.idle_timeout_sec}",
    ]


def control_supported() -> bool:
    """Best-effort: OpenSSH on Windows may lack ControlMaster."""
    if os.name == "nt":
        return True  # try anyway; fallback on failure
    return True


@dataclass
class PooledSession:
    key: tuple
    socket_path: Path
    last_used: float = field(default_factory=time.monotonic)
    in_use: bool = False


class SshSessionPool:
    _instance: SshSessionPool | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._sessions: dict[tuple, PooledSession] = {}
        self._pool_lock = threading.Lock()

    @classmethod
    def global_pool(cls) -> SshSessionPool:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def acquire(self, config: Config, *, emitter=None, thread_id: str | None = None) -> PooledSession | None:
        pool_cfg = config.execution.ssh.pool
        if not pool_cfg.enabled:
            return None
        key = pool_key(config)
        with self._pool_lock:
            self._evict_idle(pool_cfg.idle_timeout_sec)
            sess = self._sessions.get(key)
            if sess is None:
                sess = PooledSession(key=key, socket_path=control_socket_path(config))
                self._sessions[key] = sess
            sess.in_use = True
            sess.last_used = time.monotonic()
        if emitter and thread_id:
            emitter.execution_ssh_pool_acquire(thread_id, host=config.execution.ssh.host)
        return sess

    def release(self, config: Config, *, emitter=None, thread_id: str | None = None) -> None:
        pool_cfg = config.execution.ssh.pool
        if not pool_cfg.enabled:
            return
        key = pool_key(config)
        with self._pool_lock:
            sess = self._sessions.get(key)
            if sess:
                sess.in_use = False
                sess.last_used = time.monotonic()
        if emitter and thread_id:
            emitter.execution_ssh_pool_release(thread_id, host=config.execution.ssh.host)

    def _evict_idle(self, idle_timeout_sec: int) -> None:
        now = time.monotonic()
        dead = [
            k
            for k, s in self._sessions.items()
            if not s.in_use and (now - s.last_used) > idle_timeout_sec
        ]
        for k in dead:
            del self._sessions[k]


def append_pool_options(argv: list[str], config: Config) -> list[str]:
    """Insert pool ControlMaster options after ssh binary name."""
    opts = build_pool_ssh_options(config)
    if not opts:
        return argv
    if argv[0] != "ssh":
        return argv
    return [argv[0], *opts, *argv[1:]]


def log_control_unsupported_once(message: str) -> None:
    global _control_unsupported_logged
    if not _control_unsupported_logged:
        _control_unsupported_logged = True
        import sys

        print(f"[agent-cli] SSH pool: {message}", file=sys.stderr)
