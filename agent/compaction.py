from __future__ import annotations

from typing import Protocol


class Compactor(Protocol):
    """Phase 2 stub: compact long thread history into a summary."""

    def should_compact(self, token_estimate: int) -> bool:
        ...

    def compact(self, messages: list[dict]) -> list[dict]:
        ...


class NoOpCompactor:
    """Phase 1 stub — compaction disabled."""

    def should_compact(self, token_estimate: int) -> bool:
        return False

    def compact(self, messages: list[dict]) -> list[dict]:
        return messages
