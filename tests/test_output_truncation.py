from __future__ import annotations

from agent.tui.output_truncation import (
    format_tool_output_lines,
    truncate_middle_chars,
)


def test_truncate_middle_chars_short_passthrough() -> None:
    assert truncate_middle_chars("hello", 100) == "hello"


def test_truncate_middle_chars_inserts_ellipsis() -> None:
    text = "a" * 5000
    out = truncate_middle_chars(text, 200)
    assert "omitted" in out
    assert len(out.encode("utf-8")) < len(text.encode("utf-8"))


def test_format_tool_output_adds_total_lines_header() -> None:
    body = "\n".join(f"line {i}" for i in range(20))
    lines = format_tool_output_lines(body, expanded=False)
    assert lines[0].startswith("Total output lines: 20")
    assert any("more lines" in line for line in lines)


def test_format_tool_output_expanded_allows_more() -> None:
    body = "\n".join(f"line {i}" for i in range(15))
    lines = format_tool_output_lines(body, expanded=True)
    assert not lines[0].startswith("Total output lines:") or len(lines) > 10
