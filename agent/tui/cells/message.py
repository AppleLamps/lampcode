"""Render user and assistant message cells."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from agent.tui.cells.base import AssistantMessageCell, UserMessageCell
from agent.tui.markdown_render import assistant_markdown_renderable

_AGENT_HEADER = Text.from_markup("\n[bold #3fb950]Agent[/bold #3fb950]\n")


def render_user_message(cell: UserMessageCell) -> str:
    return (
        f"\n[bold #388bfd]You[/bold #388bfd]\n"
        f"  [#388bfd]│[/#388bfd] [white]{cell.text}[/white]"
    )


def assistant_message_visual(text: str, *, streaming: bool = False) -> Group | Text:
    """Rich renderable for assistant text in the transcript."""
    if not text.strip():
        return _AGENT_HEADER
    if streaming:
        body = Text(text)
        return Group(_AGENT_HEADER, body)
    return Group(_AGENT_HEADER, assistant_markdown_renderable(text))


def render_assistant_message(cell: AssistantMessageCell) -> Group | Text:
    return assistant_message_visual(cell.text, streaming=cell.streaming)
