"""Render user and assistant message cells."""

from __future__ import annotations

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text

from agent.tui.cells.base import AssistantMessageCell, UserMessageCell

_AGENT_HEADER = Text.from_markup("\n[bold #3fb950]Agent[/bold #3fb950]\n")


def render_user_message(cell: UserMessageCell) -> str:
    return (
        f"\n[bold #388bfd]You[/bold #388bfd]\n"
        f"[on #161b22] [#388bfd]│[/#388bfd] {cell.text} [/on #161b22]"
    )


def assistant_message_visual(text: str) -> Group | Text:
    """Rich renderable with full markdown processing for Textual Static."""
    if not text.strip():
        return _AGENT_HEADER
    return Group(_AGENT_HEADER, Markdown(text))


def render_assistant_message(cell: AssistantMessageCell) -> Group | Text:
    return assistant_message_visual(cell.text)
