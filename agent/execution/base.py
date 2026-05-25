from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ExecutionResult:
    output: str
    exit_code: int
    duration_ms: int
    backend: str
    meta: dict[str, Any] = field(default_factory=dict)


class ExecutionBackend(Protocol):
    @property
    def name(self) -> str: ...

    def run(
        self,
        cwd: Path,
        cmd: str,
        *,
        workdir: str | None = None,
        timeout: int = 120,
        max_output: int = 20_000,
    ) -> ExecutionResult: ...
