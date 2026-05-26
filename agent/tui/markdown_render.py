"""Markdown → Rich markup for assistant messages."""

from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```([\w+-]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_HEADER = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*]\s+(.+)$", re.MULTILINE)
_NUMBERED = re.compile(r"^(\s*)(\d+)\.\s+(.+)$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
_HRULE = re.compile(r"^---+\s*$", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _apply_inline_styles(text: str) -> str:
    text = _LINK.sub(r"[bold]\1[/bold] [dim](\2)[/dim]", text)
    text = _BOLD.sub(r"[bold]\1[/bold]", text)
    text = _ITALIC.sub(r"[italic]\1[/italic]", text)
    text = _INLINE_CODE.sub(r"[cyan on #161b22]\1[/cyan on #161b22]", text)
    return text


def _render_code_fence(match: re.Match[str]) -> str:
    lang = match.group(1).strip() or "text"
    body = match.group(2).rstrip("\n")
    header = f"[dim]```{lang}[/dim]" if lang else "[dim]```[/dim]"
    body_lines = body.splitlines() or [""]
    rendered = [header]
    for line in body_lines:
        rendered.append(f"[on #161b22] {line} [/on #161b22]")
    rendered.append("[dim]```[/dim]")
    return "\n".join(rendered)


def render_assistant_markdown(text: str) -> str:
    """Convert common markdown to Rich markup for Static cells."""
    if not text.strip():
        return ""

    result = text.replace("\r\n", "\n")

    # Code fences first (avoid inline transforms inside code)
    placeholders: dict[str, str] = {}

    def _stash_fence(match: re.Match[str]) -> str:
        key = f"@@FENCE{len(placeholders)}@@"
        placeholders[key] = _render_code_fence(match)
        return key

    result = _CODE_FENCE.sub(_stash_fence, result)

    result = _HRULE.sub("[dim]────────────────────────[/dim]", result)
    result = _HEADER.sub(
        lambda m: f"[bold]{'#' * len(m.group(1))} {_apply_inline_styles(m.group(2))}[/bold]",
        result,
    )
    result = _NUMBERED.sub(
        lambda m: f"{m.group(1)}[dim]{m.group(2)}.[/dim] {_apply_inline_styles(m.group(3))}",
        result,
    )
    result = _BULLET.sub(
        lambda m: f"{m.group(1)}[bold]•[/bold] {_apply_inline_styles(m.group(2))}",
        result,
    )
    result = _BLOCKQUOTE.sub(
        lambda m: f"[dim]│[/dim] [italic]{_apply_inline_styles(m.group(1))}[/italic]",
        result,
    )

    result = _apply_inline_styles(result)

    for key, rendered in placeholders.items():
        result = result.replace(key, rendered)

    return result
