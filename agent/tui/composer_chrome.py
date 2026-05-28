"""Format strings for the bottom composer chrome (model, mode, context)."""

from __future__ import annotations

from agent.config import Config
from agent.models import Thread
from agent.tui.formatting import format_mode_badge
from agent.tui.statusline import (
    StatuslineContext,
    format_statusline,
    load_statusline_settings,
)

__all__ = [
    "format_mode_badge",
    "format_composer_meta",
    "format_approval_label",
    "context_bar",
    "truncate_session_title",
]

from agent.tui.formatting import (  # noqa: E402 — re-export for tests
    context_bar,
    format_approval_label,
    truncate_session_title,
)


def format_composer_meta(
    *,
    config: Config,
    model_short: str,
    profile: str | None,
    plan_mode: bool,
    thread: Thread | None,
    include_context: bool,
    turn_running: bool = False,
    routing_note: str = "",
    session_auto_approve: bool = False,
    pending_approval: bool = False,
    memories_pending: int = 0,
    statusline_settings=None,
    context_snapshot=None,
    session_cost_usd: float | None = None,
    last_turn_fallback: bool = False,
) -> str:
    from agent.context_meter import build_context_snapshot

    settings = statusline_settings or load_statusline_settings(config.config_path)
    snap = context_snapshot
    if snap is None and include_context and thread is not None:
        snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    ctx = StatuslineContext(
        config=config,
        model_short=model_short,
        profile=profile,
        plan_mode=plan_mode,
        thread=thread,
        include_context=include_context,
        session_auto_approve=session_auto_approve,
        pending_approval=pending_approval,
        memories_pending=memories_pending,
        routing_note=routing_note,
        context_snapshot=snap,
        session_cost_usd=session_cost_usd,
        last_turn_fallback=last_turn_fallback,
    )
    line = format_statusline(ctx, settings)
    if turn_running:
        working = "[#58a6ff bold]working…[/]"
        return f"{line}  [dim]·[/dim]  {working}" if line else working
    return line
