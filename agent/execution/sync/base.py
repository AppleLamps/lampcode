from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class SyncResult:
    ok: bool
    direction: str
    transport: str
    files: int = 0
    bytes_transferred: int = 0
    duration_ms: int = 0
    summary: str = ""
    error: str | None = None
    argv: list[str] = field(default_factory=list)
    skipped: bool = False


class SyncTransport(Protocol):
    name: str

    def push(
        self,
        local_cwd: Path,
        remote_spec: str,
        *,
        excludes: list[str],
        delete_remote_extra: bool,
        runner=None,
    ) -> SyncResult: ...

    def pull(
        self,
        local_cwd: Path,
        remote_spec: str,
        *,
        excludes: list[str],
        runner=None,
    ) -> SyncResult: ...
