from __future__ import annotations

from agent.config import Config
from agent.context_meter import (
    ContextSnapshot,
    TokenCounter,
    build_context_snapshot,
    extract_api_context_tokens,
    invalidate_context_cache,
    snapshot_to_dict,
)
from agent.models import Thread, Turn, UserMessageItem


def test_extract_api_context_tokens_prefers_total():
    assert extract_api_context_tokens({"total_tokens": 100, "prompt_tokens": 80}) == 100
    assert extract_api_context_tokens({"prompt_tokens": 50}) == 50
    assert extract_api_context_tokens(None) is None


def test_token_counter_heuristic_without_tiktoken():
    counter = TokenCounter("unknown-model-xyz")
    assert counter.count_text("abcd") >= 1
    msgs = [{"role": "user", "content": "hello world"}]
    assert counter.count_messages(msgs) >= 1


def test_context_snapshot_baseline_headroom():
    config = Config.resolve()
    thread = Thread(cwd=str(config.cwd), title="t", model=config.model)
    thread.turns.append(
        Turn(items=[UserMessageItem(text="Say hi")], status="completed")
    )
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    assert snap.window_tokens > 0
    assert snap.effective_window <= snap.window_tokens
    assert snap.baseline_tokens >= 0
    assert snap.headroom_tokens >= 0
    assert 0 <= snap.left_pct <= 100


def test_divergence_when_api_differs():
    config = Config.resolve()
    thread = Thread(
        cwd=str(config.cwd), model=config.model, last_context_tokens=10_000
    )
    thread.turns.append(
        Turn(items=[UserMessageItem(text="x" * 100)], status="completed")
    )
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    if snap.api_context and snap.estimated_total:
        # May or may not diverge depending on estimate; force check on math
        ratio = abs(snap.estimated_total - snap.api_context) / max(snap.api_context, 1)
        assert snap.divergence == (ratio > 0.15)


def test_snapshot_to_dict_roundtrip_keys():
    snap = ContextSnapshot(
        window_tokens=128000,
        baseline_tokens=1000,
        headroom_tokens=8000,
        effective_window=119000,
        estimated_total=50000,
        api_context=48000,
        used_tokens=50000,
        used_pct=42,
        left_pct=58,
    )
    data = snapshot_to_dict(snap)
    assert data["window_tokens"] == 128000
    assert "segments" in data


def test_invalidate_context_cache():
    invalidate_context_cache()
    invalidate_context_cache("thread-abc")
