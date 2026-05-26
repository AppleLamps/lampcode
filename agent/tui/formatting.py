"""Shared Rich format helpers for TUI chrome."""

from __future__ import annotations

from agent.config import ApprovalMode


def truncate_session_title(title: str, *, max_len: int = 36) -> str:
    text = " ".join(title.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def format_approval_label(
    *,
    approval_mode: ApprovalMode,
    session_auto_approve: bool = False,
    pending_approval: bool = False,
) -> str:
    if pending_approval:
        return "[yellow bold]awaiting[/yellow bold]"
    if session_auto_approve or approval_mode == "auto":
        return "[green]auto[/green]"
    return "[#e3b341]prompt[/#e3b341]"


def context_bar(*, used_pct: int, width: int = 12) -> str:
    filled = max(0, min(width, int(round(width * used_pct / 100))))
    empty = width - filled
    if used_pct >= 90:
        color = "red"
    elif used_pct >= 70:
        color = "yellow"
    else:
        color = "green"
    return f"[{color}]{'█' * filled}[/][dim]{'░' * empty}[/]"


def format_context_line(snapshot) -> str:
    """Hybrid context display from ContextSnapshot."""
    bar = context_bar(used_pct=snapshot.used_pct)
    counts = snapshot.token_counts_label()
    left = f"[bold]{snapshot.left_pct}%[/bold] left"
    if snapshot.warn_level == "red":
        left = f"[red bold]{snapshot.left_pct}% left[/red bold]"
    elif snapshot.warn_level == "yellow":
        left = f"[yellow bold]{snapshot.left_pct}% left[/yellow bold]"
    hint = " [dim]est≠api[/dim]" if snapshot.divergence else ""
    return f"[dim]context[/dim] {bar} {counts} ({left}){hint}"


def format_mode_badge(*, plan_mode: bool) -> str:
    if plan_mode:
        return "[bold black on #d29922] PLAN [/bold black on #d29922]"
    return "[bold white on #1f6feb] CODE [/bold white on #1f6feb]"
