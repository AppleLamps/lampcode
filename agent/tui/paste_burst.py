"""Detect rapid key bursts as paste (Windows terminals often lack bracketed paste)."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field


@dataclass
class PasteBurstDetector:
    """Return a paste string when many printable keys arrive in a short window."""

    enabled: bool = field(default_factory=lambda: sys.platform == "win32")
    burst_window_sec: float = 0.04
    min_burst_chars: int = 4
    _buffer: list[str] = field(default_factory=list)
    _last_at: float = 0.0
    _first_at: float = 0.0

    def reset(self) -> None:
        self._buffer.clear()
        self._last_at = 0.0
        self._first_at = 0.0

    def feed(self, ch: str) -> str | None:
        """Record a key; return paste text when a burst completes, else None."""
        if not self.enabled or len(ch) != 1 or not ch.isprintable() or ch.isspace():
            self.reset()
            return None
        now = time.monotonic()
        if self._buffer and (now - self._last_at) > self.burst_window_sec:
            self.reset()
        if not self._buffer:
            self._first_at = now
        self._buffer.append(ch)
        self._last_at = now
        if len(self._buffer) < self.min_burst_chars:
            return None
        elapsed = now - self._first_at
        if elapsed <= self.burst_window_sec * len(self._buffer):
            text = "".join(self._buffer)
            self.reset()
            return text
        return None
