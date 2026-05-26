"""Shared diff formatting for TUI and CLI output."""

from __future__ import annotations

_CHANGE_PREFIX = {"add": "A", "update": "M", "delete": "D", "overwrite": "M"}


def format_file_summary(files: list[tuple[str, str]]) -> str:
    """Format file list as 'A foo.py  M bar.py  D baz.py'."""
    parts: list[str] = []
    for path, change_type in files:
        prefix = _CHANGE_PREFIX.get(change_type, "M")
        parts.append(f"{prefix} {path}")
    return "  ".join(parts)


def format_diff_line(line: str, *, line_number: int | None = None) -> str:
    """Format a single diff line with Rich markup."""
    num = f"{line_number:>4} " if line_number is not None else ""
    if line.startswith("+") and not line.startswith("+++"):
        return f"{num}[green]{line}[/green]"
    if line.startswith("-") and not line.startswith("---"):
        return f"{num}[red]{line}[/red]"
    if line.startswith("@@") or line.startswith("+++"):
        return f"{num}[cyan]{line}[/cyan]"
    if line.startswith("---"):
        return f"{num}[cyan]{line}[/cyan]"
    return f"{num}[dim]{line}[/dim]"


def format_diff_lines(
    diff_text: str,
    *,
    max_lines: int = 40,
    line_numbers: bool = False,
) -> list[str]:
    """Return Rich-markup lines for a unified diff snippet."""
    lines = str(diff_text).splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(str(diff_text).splitlines()) - max_lines} more lines)"]
    result: list[str] = []
    for idx, line in enumerate(lines, start=1):
        result.append(format_diff_line(line, line_number=idx if line_numbers else None))
    return result


def strip_rich_markup(text: str) -> str:
    """Remove Rich markup tags for golden test comparison."""
    import re

    return re.sub(r"\[[^\]]+\]", "", text)
