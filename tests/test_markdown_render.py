"""Tests for assistant markdown rendering."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from agent.tui.cells.base import AssistantMessageCell
from agent.tui.cells.message import assistant_message_visual, render_assistant_message
from agent.tui.diff_palette import ColorLevel, DiffPalette, DiffTheme
from agent.tui.diff_render import strip_rich_markup
from agent.tui.markdown_render import (
    assistant_markdown_renderable,
    render_assistant_markdown,
)
from agent.tui.terminal_syntax_theme import reset_syntax_theme_cache, resolve_syntax_themes


@pytest.fixture(autouse=True)
def _reset_theme_cache() -> None:
    reset_syntax_theme_cache()
    yield
    reset_syntax_theme_cache()


def _print_plain(renderable, *, width: int = 120) -> str:
    buf = StringIO()
    Console(file=buf, width=width, force_terminal=True).print(renderable)
    return buf.getvalue()


SAMPLE_MARKDOWN = """# Title

**bold** and *italic*

1. First step
2. Second step

| Case | Result |
|------|--------|
| ok   | pass   |

```python
def login():
    return True
```
"""


def test_rich_markdown_bold_and_fence() -> None:
    text = "# Title\n\n**bold** text\n\n```python\nprint(1)\n```"
    plain = _print_plain(assistant_markdown_renderable(text))
    assert "Title" in plain
    assert "bold" in plain
    assert "print(1)" in plain


def test_assistant_message_visual_uses_markdown() -> None:
    visual = render_assistant_message(AssistantMessageCell(text="**hello**"))
    plain = _print_plain(visual)
    assert "Agent" in plain
    assert "hello" in plain
    assert "**hello**" not in plain


def test_markdown_table_and_list() -> None:
    plain = render_assistant_markdown(SAMPLE_MARKDOWN, width=100)
    assert "Title" in plain
    assert "bold" in plain
    assert "First step" in plain
    assert "Case" in plain
    assert "login" in plain


def test_markdown_width_80() -> None:
    plain = render_assistant_markdown(SAMPLE_MARKDOWN, width=80)
    assert "Title" in plain
    assert len(plain) > 20


def test_terminal_adaptive_code_theme() -> None:
    dark = resolve_syntax_themes(
        DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    )
    light = resolve_syntax_themes(
        DiffPalette(theme=DiffTheme.LIGHT, level=ColorLevel.TRUECOLOR)
    )
    assert dark[0] == "github-dark"
    assert light[0] == "github-light"


def test_legacy_plain_renderer() -> None:
    plain = strip_rich_markup(render_assistant_markdown("**bold**"))
    assert "bold" in plain
