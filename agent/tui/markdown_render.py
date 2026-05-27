"""Markdown rendering for assistant messages (Rich Markdown pipeline)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.markdown import Markdown

from agent.tui.terminal_syntax_theme import resolve_syntax_themes


def assistant_markdown_renderable(text: str) -> Markdown:
    """Rich Markdown with terminal-adaptive code-block themes."""
    code_theme, inline_theme = resolve_syntax_themes()
    return Markdown(
        text,
        code_theme=code_theme,
        inline_code_theme=inline_theme,
        hyperlinks=True,
    )


def render_assistant_markdown(text: str, *, width: int = 120) -> str:
    """Plain-text preview of assistant markdown at a given terminal width."""
    if not text.strip():
        return ""
    buf = StringIO()
    Console(file=buf, width=width, force_terminal=True).print(
        assistant_markdown_renderable(text)
    )
    return buf.getvalue()


