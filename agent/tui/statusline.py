"""Configurable TUI status line (Codex-style /statusline items)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.config import Config
from agent.git import detect_repo_root
from agent.models import Thread
from agent.context_meter import build_context_snapshot
from agent.tui.formatting import format_context_line
from agent.tui.context_usage import format_token_count

StatuslineItem = Literal[
    "model",
    "profile",
    "mode",
    "sandbox",
    "approvals",
    "context",
    "session",
    "turns",
    "branch",
    "cwd",
    "memories",
]

DEFAULT_STATUSLINE: list[StatuslineItem] = [
    "model",
    "mode",
    "sandbox",
    "approvals",
    "context",
    "session",
    "turns",
]


@dataclass
class StatuslineSettings:
    items: list[StatuslineItem] = field(default_factory=lambda: list(DEFAULT_STATUSLINE))


@dataclass
class TuiSettings:
    """Chrome preferences loaded from `[tui]` in config.toml."""

    statusline: StatuslineSettings = field(default_factory=StatuslineSettings)
    reduced_motion: bool = False


def _env_reduced_motion() -> bool:
    return os.environ.get("AGENT_TUI_REDUCED_MOTION", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def load_tui_settings(path: Path | None = None) -> TuiSettings:
    """Load statusline items and accessibility flags from config + env."""
    statusline = load_statusline_settings(path)
    reduced_motion = _env_reduced_motion()
    from agent.paths import default_config_path

    config_path = path or default_config_path()
    if config_path.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:
            return TuiSettings(statusline=statusline, reduced_motion=reduced_motion)
        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
            tui = data.get("tui", {})
            if isinstance(tui, dict) and bool(tui.get("reduced_motion", False)):
                reduced_motion = True
        except (OSError, ValueError):
            pass
    return TuiSettings(statusline=statusline, reduced_motion=reduced_motion)


def load_statusline_settings(path: Path | None = None) -> StatuslineSettings:
    from agent.paths import default_config_path

    config_path = path or default_config_path()
    if not config_path.is_file():
        return StatuslineSettings()

    try:
        import tomllib
    except ModuleNotFoundError:
        return StatuslineSettings()

    with config_path.open("rb") as f:
        data = tomllib.load(f)
    tui = data.get("tui", {})
    if not isinstance(tui, dict):
        return StatuslineSettings()
    raw = tui.get("statusline", tui.get("statusline_items"))
    if raw is None:
        return StatuslineSettings()
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        return StatuslineSettings()
    allowed: set[str] = {
        "model",
        "profile",
        "mode",
        "sandbox",
        "approvals",
        "context",
        "session",
        "turns",
        "branch",
        "cwd",
        "memories",
    }
    items: list[StatuslineItem] = []
    for entry in raw:
        key = str(entry).strip().lower()
        if key in allowed:
            items.append(key)  # type: ignore[arg-type]
    return StatuslineSettings(items=items or list(DEFAULT_STATUSLINE))


def git_branch(cwd: Path) -> str | None:
    if not detect_repo_root(cwd):
        return None
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        return branch or "HEAD"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


@dataclass(frozen=True)
class StatuslineContext:
    config: Config
    model_short: str
    profile: str | None
    plan_mode: bool
    thread: Thread | None
    include_context: bool
    session_auto_approve: bool = False
    pending_approval: bool = False
    memories_pending: int = 0
    routing_note: str = ""
    context_snapshot: Any = None


def build_statusline_segments(ctx: StatuslineContext, settings: StatuslineSettings) -> list[str]:
    from agent.tui.formatting import (
        context_bar,
        format_approval_label,
        truncate_session_title,
    )

    segments: list[str] = []
    for item in settings.items:
        if item == "model":
            segments.append(f"[dim]model[/dim] [bold]{ctx.model_short}[/bold]")
        elif item == "profile" and ctx.profile:
            segments.append(f"[dim]profile[/dim] [bold]{ctx.profile}[/bold]")
        elif item == "mode":
            label = "plan" if ctx.plan_mode else "code"
            color = "yellow" if ctx.plan_mode else "#58a6ff"
            segments.append(f"[dim]mode[/dim] [{color}]{label}[/]")
        elif item == "sandbox":
            sandbox = ctx.config.sandbox_mode.value
            if sandbox == "danger-full-access":
                segments.append(f"[dim]sandbox[/dim] [red bold]{sandbox}[/red bold]")
            else:
                segments.append(f"[dim]sandbox[/dim] [bold]{sandbox}[/bold]")
        elif item == "approvals":
            segments.append(
                f"[dim]approvals[/dim] {format_approval_label(approval_mode=ctx.config.approval_mode, session_auto_approve=ctx.session_auto_approve, pending_approval=ctx.pending_approval)}"
            )
        elif item == "context" and ctx.include_context:
            snap = ctx.context_snapshot
            if snap is None and ctx.thread is not None:
                snap = build_context_snapshot(ctx.config, ctx.thread, ctx_settings=ctx.config.context)
            if snap is not None:
                segments.append(format_context_line(snap))
            else:
                window = format_token_count(ctx.config.context_window_tokens)
                segments.append(
                    f"[dim]context[/dim] {context_bar(used_pct=0)} [bold]0 / {window}[/bold]"
                )
        elif item == "session" and ctx.thread is not None:
            title = ctx.thread.title or ctx.thread.display_label()
            segments.append(
                f"[dim]session[/dim] [bold]{truncate_session_title(title)}[/bold]"
            )
        elif item == "turns" and ctx.thread and ctx.thread.turns:
            segments.append(f"[dim]turns[/dim] [bold]{len(ctx.thread.turns)}[/bold]")
        elif item == "branch":
            branch = git_branch(ctx.config.cwd)
            if branch:
                segments.append(f"[dim]branch[/dim] [bold]{branch}[/bold]")
        elif item == "cwd":
            segments.append(f"[dim]cwd[/dim] [bold]{ctx.config.cwd.name}[/bold]")
        elif item == "memories" and ctx.memories_pending > 0:
            segments.append(
                f"[dim]memories[/dim] [yellow bold]{ctx.memories_pending} pending[/yellow bold]"
            )
    if ctx.routing_note:
        segments.append(f"[dim]{ctx.routing_note}[/dim]")
    return segments


def format_statusline(ctx: StatuslineContext, settings: StatuslineSettings) -> str:
    segments = build_statusline_segments(ctx, settings)
    if not segments:
        return ""
    return "  [dim]·[/dim]  ".join(segments)
