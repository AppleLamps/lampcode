"""Thread-safe messages for worker → UI event delivery."""

from __future__ import annotations

from textual.message import Message


class AgentEventMessage(Message):
    """Drain the pending agent event queue on the UI thread."""


class WorkerFinishedMessage(Message):
    """Turn worker thread exited normally."""


class WorkerErrorMessage(Message):
    """Turn worker thread raised an exception."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error
