from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.cancel import CancelToken


class ActiveTurnRegistry:
    _instance: ActiveTurnRegistry | None = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, CancelToken] = {}
        self._turn_ids: dict[str, str] = {}

    @classmethod
    def global_registry(cls) -> ActiveTurnRegistry:
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def register(self, thread_id: str, turn_id: str, token: CancelToken) -> None:
        with self._lock:
            self._tokens[thread_id] = token
            self._turn_ids[thread_id] = turn_id

    def unregister(self, thread_id: str) -> None:
        with self._lock:
            self._tokens.pop(thread_id, None)
            self._turn_ids.pop(thread_id, None)

    def cancel(self, thread_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(thread_id)
        if token is None:
            return False
        token.cancel()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()

    def active_turn_id(self, thread_id: str) -> str | None:
        with self._lock:
            return self._turn_ids.get(thread_id)

    def is_active(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._tokens
