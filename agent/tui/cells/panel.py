"""Shared collapsible panel chrome for tool/diff cells."""

from __future__ import annotations


def expand_affordance(*, expanded: bool, lines_hidden: int = 0) -> str:
    if expanded:
        return "[dim]▾ expanded[/dim]"
    if lines_hidden > 0:
        return f"[dim]▸ {lines_hidden} lines — click or press e[/dim]"
    return "[dim]▸ click or press e[/dim]"


def panel_top_border() -> str:
    return "[dim]┌─[/dim]"


def panel_bottom_border() -> str:
    return "[dim]└─[/dim]"


def indent_block(lines: list[str], *, prefix: str = "  ") -> list[str]:
    return [f"{prefix}{line}" if line else prefix for line in lines]
