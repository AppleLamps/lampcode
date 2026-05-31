"""Shared collapsible panel chrome for tool/diff cells."""

from __future__ import annotations


def expand_affordance(*, expanded: bool, lines_hidden: int = 0) -> str:
    if expanded:
        return "[dim]expanded[/dim]"
    if lines_hidden > 0:
        return f"[dim]{lines_hidden} hidden[/dim]"
    return "[dim]collapsed[/dim]"


def panel_top_border() -> str:
    return ""


def panel_bottom_border() -> str:
    return ""


def indent_block(lines: list[str], *, prefix: str = "  ") -> list[str]:
    return [f"{prefix}{line}" if line else prefix for line in lines]
