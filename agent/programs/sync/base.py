from __future__ import annotations

from typing import Protocol


class ProgramSyncBackend(Protocol):
    name: str

    def push(self, program_id: str, payload: bytes) -> None: ...

    def pull(self, program_id: str) -> bytes | None: ...

    def list_program_ids(self) -> list[str]: ...
