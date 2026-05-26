from __future__ import annotations

from dataclasses import dataclass

from agent.config import Config
from agent.context_meter import ContextSnapshot, build_context_snapshot
from agent.models import Thread


@dataclass(frozen=True)
class ContextUsage:
    """Backward-compatible view for TUI and tests."""

    used_tokens: int
    window_tokens: int
    used_pct: int
    left_pct: int
    snapshot: ContextSnapshot | None = None

    def footer_label(self) -> str:
        return f"Context {self.left_pct}% left · {self.used_pct}% used"

    def status_suffix(self) -> str:
        return f" · {self.footer_label()}"

    def token_counts_label(self) -> str:
        if self.snapshot:
            return self.snapshot.token_counts_label()
        return f"{format_token_count(self.used_tokens)} / {format_token_count(self.window_tokens)}"


def format_token_count(tokens: int) -> str:
    if tokens >= 1_000_000:
        value = tokens / 1_000_000
        text = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"{text}M"
    if tokens >= 10_000:
        return f"{tokens // 1000}k"
    if tokens >= 1000:
        value = tokens / 1000
        text = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    return str(tokens)


def resolve_context_window_tokens(config: Config) -> int:
    from agent.context_meter import resolve_window_tokens

    return resolve_window_tokens(config)


def compute_context_usage(config: Config, thread: Thread | None) -> ContextUsage:
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    return ContextUsage(
        used_tokens=snap.used_tokens,
        window_tokens=snap.window_tokens,
        used_pct=snap.used_pct,
        left_pct=snap.left_pct,
        snapshot=snap,
    )
