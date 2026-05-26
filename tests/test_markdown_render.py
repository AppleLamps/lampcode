"""Tests for assistant markdown rendering."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.markdown import Markdown

from agent.tui.cells.base import AssistantMessageCell
from agent.tui.cells.message import assistant_message_visual, render_assistant_message
from agent.tui.markdown_render import render_assistant_markdown
from agent.tui.diff_render import strip_rich_markup


def _print_plain(renderable) -> str:
    buf = StringIO()
    Console(file=buf, width=120, force_terminal=True).print(renderable)
    return buf.getvalue()


def test_rich_markdown_bold_and_fence() -> None:
    text = "# Title\n\n**bold** text\n\n```python\nprint(1)\n```"
    plain = _print_plain(Markdown(text))
    assert "Title" in plain
    assert "bold" in plain
    assert "print(1)" in plain


def test_assistant_message_visual_uses_markdown() -> None:
    visual = render_assistant_message(AssistantMessageCell(text="**hello**"))
    plain = _print_plain(visual)
    assert "Agent" in plain
    assert "hello" in plain
    assert "**hello**" not in plain


def test_legacy_markup_renderer_bold() -> None:
    plain = strip_rich_markup(render_assistant_markdown("**bold**"))
    assert "bold" in plain
