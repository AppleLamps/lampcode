from __future__ import annotations

from enum import Enum


class SandboxMode(str, Enum):
    DANGER_FULL_ACCESS = "danger-full-access"
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"

    @classmethod
    def from_str(cls, value: str | None) -> SandboxMode:
        if not value:
            return cls.DANGER_FULL_ACCESS
        normalized = value.strip().lower()
        for mode in cls:
            if mode.value == normalized:
                return mode
        raise ValueError(
            f"Invalid sandbox mode: {value!r}. "
            f"Use one of: {', '.join(m.value for m in cls)}"
        )
