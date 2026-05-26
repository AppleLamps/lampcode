"""Tests for diff_render module."""

from agent.tui.diff_render import (
    format_diff_line,
    format_diff_lines,
    format_file_summary,
    strip_rich_markup,
)


def test_format_file_summary() -> None:
    result = format_file_summary([("foo.py", "add"), ("bar.py", "update"), ("baz.py", "delete")])
    assert "A foo.py" in result
    assert "M bar.py" in result
    assert "D baz.py" in result


def test_format_diff_line_addition() -> None:
    line = format_diff_line("+added line")
    assert "green" in line
    assert "+added line" in line


def test_format_diff_line_deletion() -> None:
    line = format_diff_line("-removed line")
    assert "red" in line


def test_format_diff_lines_truncates() -> None:
    diff = "\n".join(f"+line {i}" for i in range(50))
    lines = format_diff_lines(diff, max_lines=10)
    assert len(lines) == 11
    assert "more lines" in lines[-1]


def test_strip_rich_markup() -> None:
    assert strip_rich_markup("[green]+line[/green]") == "+line"
