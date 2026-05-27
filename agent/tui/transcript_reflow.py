"""Debounced transcript rebuild after terminal width changes (Codex-style)."""

from __future__ import annotations

from dataclasses import dataclass, field

REFLOW_DEBOUNCE_SEC = 0.075


@dataclass
class TranscriptReflowState:
    """
    Tracks observed vs rebuilt terminal width.

    Codex separates these because terminals can report intermediate widths
    during drag-resize before settling on the final size.
    """

    last_observed_width: int | None = None
    last_reflow_width: int | None = None
    pending_reflow_width: int | None = None
    resize_during_stream: bool = field(default=False)

    def observe_width(self, width: int) -> bool:
        """Record width; return True when it changed from the previous observation."""
        if self.last_observed_width is None:
            self.last_observed_width = width
            return False
        if width == self.last_observed_width:
            return False
        self.last_observed_width = width
        self.pending_reflow_width = width
        return True

    def needs_rebuild(self, width: int) -> bool:
        if self.pending_reflow_width is not None:
            return True
        return self.last_reflow_width != width

    def mark_rebuilt(self, width: int) -> None:
        self.last_reflow_width = width
        self.pending_reflow_width = None
        self.resize_during_stream = False

    def clear(self) -> None:
        self.last_observed_width = None
        self.last_reflow_width = None
        self.pending_reflow_width = None
        self.resize_during_stream = False
