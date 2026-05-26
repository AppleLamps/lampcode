"""Render in-transcript working / thinking indicator."""

from __future__ import annotations

from agent.tui.cells.base import WorkingCell

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def render_working_cell(cell: WorkingCell, *, frame: int = 0) -> str:
    glyph = _SPINNER[frame % len(_SPINNER)]
    return f"\n[#58a6ff]{glyph}[/] [dim italic]{cell.message}[/dim italic]"
