from __future__ import annotations

from dataclasses import dataclass

from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens, load_project_rules
from agent.models import Thread
from agent.providers.openrouter import ModelsCache, model_context_window


@dataclass(frozen=True)
class ContextUsage:
    used_tokens: int
    window_tokens: int
    used_pct: int
    left_pct: int

    def footer_label(self) -> str:
        return f"Context {self.left_pct}% left · {self.used_pct}% used"

    def status_suffix(self) -> str:
        return f" · {self.footer_label()}"


def resolve_context_window_tokens(config: Config) -> int:
    cached = ModelsCache().load()
    if cached:
        from_cache = model_context_window(config.model, cached)
        if from_cache:
            return from_cache
    return config.context_window_tokens


def compute_context_usage(config: Config, thread: Thread | None) -> ContextUsage:
    window = max(1, resolve_context_window_tokens(config))
    if thread is None or not thread.turns:
        return ContextUsage(used_tokens=0, window_tokens=window, used_pct=0, left_pct=100)

    messages = build_thread_messages(
        thread,
        project_rules=load_project_rules(config.cwd),
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
    )
    used = estimate_tokens(messages)
    used_pct = min(100, int(round(100 * used / window)))
    left_pct = max(0, 100 - used_pct)
    return ContextUsage(
        used_tokens=used,
        window_tokens=window,
        used_pct=used_pct,
        left_pct=left_pct,
    )
