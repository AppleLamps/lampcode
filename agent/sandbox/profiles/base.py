from __future__ import annotations

from typing import Any


class SandboxProfileBase:
    name: str = "base"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def available(self) -> bool:
        return True

    def apply(self, *, cmd: str, cwd: str, settings):
        raise NotImplementedError
