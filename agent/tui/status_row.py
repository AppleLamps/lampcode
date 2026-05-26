"""Working status row with spinner and elapsed time."""

from __future__ import annotations

import time

from textual.widgets import Static

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class StatusRow:
    """Manages the #status_row widget during agent turns."""

    def __init__(self, widget: Static) -> None:
        self._widget = widget
        self._running = False
        self._start_time: float = 0.0
        self._frame = 0
        self._interval = None
        self._turn_end_message: str = ""

    def start_turn(self) -> None:
        self._running = True
        self._start_time = time.monotonic()
        self._frame = 0
        self._turn_end_message = ""
        self._widget.display = True
        self._update()

    def stop_turn(self, message: str = "") -> None:
        self._running = False
        if self._interval:
            self._interval.stop()
            self._interval = None
        if message:
            self._turn_end_message = message
            self._widget.update(f"[dim]{message}[/dim]")
            self._widget.display = True
        else:
            self._widget.display = False

    def tick(self) -> None:
        if self._running:
            self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
            self._update()

    def _update(self) -> None:
        if not self._running:
            return
        elapsed = int(time.monotonic() - self._start_time)
        spinner = _SPINNER_FRAMES[self._frame]
        self._widget.update(
            f"[#58a6ff]{spinner}[/] [bold]Working[/bold] "
            f"[dim]({elapsed}s · Ctrl+C to interrupt)[/dim]"
        )

    def bind_interval(self, app) -> None:
        """Start spinner ticks when the first turn runs (not at app mount)."""
        if self._interval is None:
            self._interval = app.set_interval(0.1, self.tick)

    @property
    def interval_active(self) -> bool:
        return self._interval is not None
