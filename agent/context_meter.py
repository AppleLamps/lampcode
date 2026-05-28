"""Unified context metering: API usage, token estimates, baseline, and segments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.config import Config
from agent.context import (
    build_system_prompt,
    build_thread_messages,
    estimate_tokens,
    load_project_rules,
)
from agent.models import Thread
from agent.providers.openrouter import ModelsCache, model_context_window
from agent.settings import ContextSettings, load_context_settings

DIVERGENCE_THRESHOLD = 0.15

_segment_cache: dict[str, tuple[list["ContextSegment"], int]] = {}


@dataclass(frozen=True)
class ContextSegment:
    name: str
    tokens: int
    detail: str = ""


@dataclass(frozen=True)
class ContextSnapshot:
    window_tokens: int
    baseline_tokens: int
    headroom_tokens: int
    effective_window: int
    estimated_total: int
    api_context: int | None
    used_tokens: int
    used_pct: int
    left_pct: int
    segments: tuple[ContextSegment, ...] = ()
    divergence: bool = False
    warn_level: Literal["ok", "yellow", "red"] = "ok"

    def token_counts_label(self) -> str:
        from agent.tui.context_usage import format_token_count

        if self.api_context is not None:
            return (
                f"est {format_token_count(self.estimated_total)} / "
                f"api {format_token_count(self.api_context)} / "
                f"{format_token_count(self.window_tokens)}"
            )
        return (
            f"{format_token_count(self.used_tokens)} / "
            f"{format_token_count(self.window_tokens)}"
        )


class TokenCounter:
    """Count tokens via tiktoken when available, else heuristic."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._encoding = None
        self._tiktoken = False
        try:
            import tiktoken

            try:
                self._encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoding = tiktoken.get_encoding("cl100k_base")
            self._tiktoken = True
        except ImportError:
            self._encoding = None

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        if self._tiktoken and self._encoding is not None:
            return len(self._encoding.encode(text))
        return max(1, len(text) // 4)

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        if self._tiktoken and self._encoding is not None:
            total = 0
            for msg in messages:
                content = msg.get("content")
                text = content if isinstance(content, str) else (str(content) if content else "")
                total += len(self._encoding.encode(text))
                for tc in msg.get("tool_calls") or []:
                    total += len(self._encoding.encode(json.dumps(tc)))
            return total
        return estimate_tokens(messages)


def extract_api_context_tokens(usage: dict[str, Any] | None) -> int | None:
    if not usage:
        return None
    total = usage.get("total_tokens")
    if total is not None:
        return int(total)
    prompt = usage.get("prompt_tokens")
    if prompt is not None:
        return int(prompt)
    return None


def resolve_window_tokens(config: Config) -> int:
    cached = ModelsCache().load()
    if cached:
        from_cache = model_context_window(config.model, cached)
        if from_cache:
            return from_cache
    return max(1, config.context_window_tokens)


def _config_fingerprint(config: Config) -> str:
    parts = [
        config.model,
        str(config.context_window_tokens),
        str(config.max_tool_output),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _cache_key(thread: Thread, config: Config) -> str:
    return f"{thread.id}:{thread.updated_at}:{_config_fingerprint(config)}"


def _measure_baseline(
    config: Config,
    *,
    project_rules: str,
    ctx_settings: ContextSettings,
) -> int:
    if ctx_settings.baseline_mode == "none":
        return 0
    if ctx_settings.baseline_mode == "fixed":
        return max(0, ctx_settings.baseline_tokens)
    cwd = Path(config.cwd)
    rules_path = cwd / "AGENTS.md"
    rules_mtime = str(rules_path.stat().st_mtime) if rules_path.is_file() else "0"
    cache_id = f"baseline:{config.cwd}:{config.model}:{rules_mtime}"
    if cache_id in _segment_cache:
        segs, total = _segment_cache[cache_id]
        if segs:
            return total
    counter = TokenCounter(config.model)
    system_text = build_system_prompt(
        config.cwd,
        None,
        project_rules=project_rules,
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
    )
    tokens = counter.count_text(system_text)
    _segment_cache[cache_id] = ([ContextSegment("baseline", tokens, "system prompt")], tokens)
    return tokens


def _headroom_tokens(config: Config, window: int, ctx_settings: ContextSettings) -> int:
    fixed = max(0, ctx_settings.headroom_tokens)
    pct = max(0.0, min(0.5, ctx_settings.headroom_pct))
    return fixed + int(window * pct)


def _build_segments(
    thread: Thread,
    config: Config,
    *,
    project_rules: str,
    counter: TokenCounter,
) -> list[ContextSegment]:
    segments: list[ContextSegment] = []
    system_text = build_system_prompt(
        Path(thread.cwd),
        thread.repo_root,
        project_rules=project_rules,
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
    )
    segments.append(
        ContextSegment("system", counter.count_text(system_text), "agent instructions")
    )
    if project_rules.strip():
        segments.append(
            ContextSegment(
                "rules",
                counter.count_text(project_rules),
                "AGENTS.md",
            )
        )

    from agent.context import build_messages_from_turn_items

    for t_idx, turn in enumerate(thread.turns):
        item_msgs = build_messages_from_turn_items(turn.items)
        turn_tokens = counter.count_messages(item_msgs)
        label = f"turn:{t_idx + 1}"
        detail = turn.status
        for item in turn.items:
            if item.type == "commandExecution" and item.output:
                out_len = len(item.output or "")
                if out_len > 2000:
                    detail = f"run_command ({out_len} chars)"
                    break
        segments.append(ContextSegment(label, turn_tokens, detail))

    return segments


def build_context_snapshot(
    config: Config,
    thread: Thread | None,
    *,
    ctx_settings: ContextSettings | None = None,
) -> ContextSnapshot:
    ctx_settings = ctx_settings or load_context_settings(config.config_path)
    window = resolve_window_tokens(config)
    if thread is None or not thread.turns:
        baseline = _measure_baseline(config, project_rules="", ctx_settings=ctx_settings)
        headroom = _headroom_tokens(config, window, ctx_settings)
        effective = max(1, window - baseline - headroom)
        return ContextSnapshot(
            window_tokens=window,
            baseline_tokens=baseline,
            headroom_tokens=headroom,
            effective_window=effective,
            estimated_total=baseline,
            api_context=thread.last_context_tokens if thread else None,
            used_tokens=0,
            used_pct=0,
            left_pct=100,
            segments=(),
            warn_level="ok",
        )

    project_rules, _ = load_project_rules(config.cwd)
    cache_key = _cache_key(thread, config)
    counter = TokenCounter(config.model)
    messages = build_thread_messages(
        thread,
        project_rules=project_rules,
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
    )
    estimated = counter.count_messages(messages)

    if cache_key not in _segment_cache:
        segs = _build_segments(thread, config, project_rules=project_rules, counter=counter)
        _segment_cache[cache_key] = (segs, estimated)
    segments, _ = _segment_cache[cache_key]

    baseline = _measure_baseline(config, project_rules=project_rules, ctx_settings=ctx_settings)
    headroom = _headroom_tokens(config, window, ctx_settings)
    effective = max(1, window - baseline - headroom)

    api_context = thread.last_context_tokens
    used = max(estimated, api_context or 0)
    used_pct = min(100, int(round(100 * used / effective)))
    left_pct = max(0, 100 - used_pct)

    divergence = False
    if api_context is not None and api_context > 0:
        divergence = abs(estimated - api_context) / api_context > DIVERGENCE_THRESHOLD

    warn: Literal["ok", "yellow", "red"] = "ok"
    if left_pct < ctx_settings.warn_red_left_pct:
        warn = "red"
    elif left_pct < ctx_settings.warn_yellow_left_pct:
        warn = "yellow"

    return ContextSnapshot(
        window_tokens=window,
        baseline_tokens=baseline,
        headroom_tokens=headroom,
        effective_window=effective,
        estimated_total=estimated,
        api_context=api_context,
        used_tokens=used,
        used_pct=used_pct,
        left_pct=left_pct,
        segments=tuple(segments),
        divergence=divergence,
        warn_level=warn,
    )


def invalidate_context_cache(thread_id: str | None = None) -> None:
    if thread_id is None:
        _segment_cache.clear()
        return
    keys = [k for k in _segment_cache if k.startswith(f"{thread_id}:")]
    for k in keys:
        del _segment_cache[k]


def format_context_breakdown(snapshot: ContextSnapshot, *, top_n: int = 8) -> str:
    lines = [
        f"Window: {snapshot.window_tokens:,} tokens",
        f"Baseline: {snapshot.baseline_tokens:,} · Headroom: {snapshot.headroom_tokens:,}",
        f"Effective: {snapshot.effective_window:,}",
        f"Estimated: {snapshot.estimated_total:,}",
    ]
    if snapshot.api_context is not None:
        lines.append(f"API (last call): {snapshot.api_context:,}")
    lines.append(f"Used: {snapshot.used_pct}% · Left: {snapshot.left_pct}%")
    if snapshot.divergence:
        lines.append("Note: estimate and API usage diverge (>15%)")
    lines.append("")
    lines.append("Segments (largest first):")
    ranked = sorted(snapshot.segments, key=lambda s: s.tokens, reverse=True)[:top_n]
    for seg in ranked:
        detail = f" — {seg.detail}" if seg.detail else ""
        lines.append(f"  {seg.name}: {seg.tokens:,}{detail}")
    return "\n".join(lines)


def snapshot_to_dict(snapshot: ContextSnapshot) -> dict[str, Any]:
    return {
        "window_tokens": snapshot.window_tokens,
        "baseline_tokens": snapshot.baseline_tokens,
        "headroom_tokens": snapshot.headroom_tokens,
        "effective_window": snapshot.effective_window,
        "estimated_total": snapshot.estimated_total,
        "api_context": snapshot.api_context,
        "used_tokens": snapshot.used_tokens,
        "used_pct": snapshot.used_pct,
        "left_pct": snapshot.left_pct,
        "divergence": snapshot.divergence,
        "warn_level": snapshot.warn_level,
        "segments": [
            {"name": s.name, "tokens": s.tokens, "detail": s.detail} for s in snapshot.segments
        ],
    }
