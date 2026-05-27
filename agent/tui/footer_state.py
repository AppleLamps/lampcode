"""Footer/composer chrome state machine with width-aware hint collapse."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FooterMode(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    APPROVAL = "approval"
    USER_INPUT = "user_input"
    LOADING = "loading"
    REVERSE_SEARCH = "reverse_search"
    RESUME_PREVIEW = "resume_preview"


@dataclass(frozen=True)
class FooterProps:
    mode: FooterMode
    app_version: str
    screen_mode: str  # home | chat | resume
    turn_running: bool = False
    threads_loading: bool = False
    reverse_search_query: str = ""
    reverse_search_match: str = ""
    resume_preview: str = ""
    terminal_width: int = 120


def _join_hints(parts: list[str], *, max_width: int) -> str:
    if not parts:
        return ""
    text = "  [dim]·[/dim]  ".join(parts)
    plain = text.replace("[dim]", "").replace("[/dim]", "")
    plain = plain.replace("[bold]", "").replace("[/bold]", "")
    if len(plain) <= max_width:
        return text
    trimmed = list(parts)
    while trimmed and len("  ·  ".join(trimmed)) > max_width:
        trimmed.pop()
    if not trimmed:
        return parts[0]
    return "  [dim]·[/dim]  ".join(trimmed)


def format_footer(props: FooterProps) -> str:
    w = max(40, props.terminal_width - 6)

    if props.mode == FooterMode.REVERSE_SEARCH:
        q = props.reverse_search_query or "(type to search)"
        preview = props.reverse_search_match[:80] if props.reverse_search_match else "—"
        line = (
            f"[cyan bold]reverse[/cyan bold] [dim]query[/dim] {q!r}  "
            f"[dim]·[/dim]  [dim]preview[/dim] {preview}  "
            f"[dim]·[/dim]  [dim]Enter accept · Esc cancel[/dim]"
        )
        return line[: w + 40]

    if props.mode == FooterMode.APPROVAL:
        hints = [
            "[dim]y[/dim] yes",
            "[dim]n[/dim] no",
            "[dim]a[/dim] all",
            "[dim]A[/dim] session",
            "[dim]Ctrl+T[/dim] transcript",
        ]
        return _join_hints(hints, max_width=w)

    if props.mode == FooterMode.USER_INPUT:
        hints = [
            "[cyan bold]answer required[/cyan bold]",
            "[dim]Enter[/dim] submit",
            "[dim]Esc[/dim] cancel",
        ]
        return _join_hints(hints, max_width=w)

    if props.mode == FooterMode.WORKING:
        hints = [
            "[#58a6ff bold]⠋ working[/]",
            "[dim]Ctrl+C[/dim] cancel",
            "[dim]/model /plan[/dim]",
            "[dim]Ctrl+T[/dim] transcript",
            f"[dim]agent {props.app_version}[/dim]",
        ]
        return _join_hints(hints, max_width=w)

    if props.mode == FooterMode.LOADING:
        return "[dim]⠋ Loading sessions…[/dim]"

    if props.mode == FooterMode.RESUME_PREVIEW:
        preview = props.resume_preview or "[dim]Select a session[/dim]"
        hints = [preview[: min(60, w - 10)], "[dim]Enter[/dim] open · [dim]Esc[/dim] back"]
        return _join_hints(hints, max_width=w)

    if props.screen_mode == "home":
        hints = [
            "[dim]Enter[/dim] new",
            "[dim]Ctrl+S[/dim] resume",
            "[dim]Ctrl+R[/dim] history",
            "[dim]Ctrl+Q[/dim] quit",
            f"[dim]agent {props.app_version}[/dim]",
        ]
        return _join_hints(hints, max_width=w)

    hints = [
        "[dim]Enter[/dim] send",
        "[dim]Shift+Enter[/dim] newline",
        "[dim]Ctrl+R[/dim] history",
        "[dim]Ctrl+T[/dim] transcript",
        "[dim]e[/dim] expand",
        "[dim]Ctrl+W[/dim] new",
        f"[dim]agent {props.app_version}[/dim]",
    ]
    return _join_hints(hints, max_width=w)


def resolve_footer_mode(
    *,
    pending_approval: bool,
    pending_user_input: bool = False,
    turn_running: bool,
    threads_loading: bool,
    screen_mode: str,
    reverse_search_active: bool,
    resume_highlight: bool,
) -> FooterMode:
    if reverse_search_active:
        return FooterMode.REVERSE_SEARCH
    if pending_user_input:
        return FooterMode.USER_INPUT
    if pending_approval:
        return FooterMode.APPROVAL
    if turn_running:
        return FooterMode.WORKING
    if threads_loading and screen_mode == "home":
        return FooterMode.LOADING
    if resume_highlight and screen_mode == "resume":
        return FooterMode.RESUME_PREVIEW
    return FooterMode.IDLE
