from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class KernelWrapResult:
    applied: bool
    backend: str
    wrapped_cmd: str | None = None
    argv: list[str] | None = None
    shell: bool = True
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    isolation_level: str | None = None


class KernelSandboxBackend(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def wrap_command(
        self,
        *,
        cmd: str,
        cwd: str,
        sandbox_mode: str,
        settings,
    ) -> KernelWrapResult:
        ...
