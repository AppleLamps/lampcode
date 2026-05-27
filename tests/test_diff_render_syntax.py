from __future__ import annotations

from agent.tui.diff_render import format_diff_line, lexer_for_path


def test_lexer_for_python_path() -> None:
    assert lexer_for_path("src/auth.py") == "python"


def test_format_diff_line_addition_without_syntax() -> None:
    line = format_diff_line("+def foo(): pass", syntax_highlight=False)
    assert "[green]" in line
    assert "def foo" in line


def test_format_diff_line_addition_with_syntax() -> None:
    line = format_diff_line(
        "+def foo(): pass",
        lexer="python",
        syntax_highlight=True,
    )
    assert "+" in line or "def" in line
