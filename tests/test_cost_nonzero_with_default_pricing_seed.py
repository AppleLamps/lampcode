from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Config
from agent.models import Thread, Turn, Usage, UserMessageItem
from agent.profiles import thread_cost_summary
from agent.providers.openrouter import enrich_usage
from agent.store import ThreadStore


def test_default_pricing_seed_nonzero_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = Config.resolve(cwd=tmp_path, model="anthropic/claude-sonnet-4")
    assert "anthropic/claude-sonnet-4" in cfg.openrouter.pricing
    out = enrich_usage(
        cfg,
        model_used="anthropic/claude-sonnet-4",
        fallback_used=False,
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
    )
    assert out["estimated_cost_usd"] > 0


def test_threads_cost_nonzero_with_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    cfg = Config.resolve(cwd=tmp_path, model="anthropic/claude-sonnet-4")
    store = ThreadStore()
    thread = Thread(id="cost-thread", cwd=str(tmp_path), model=cfg.model)
    turn = Turn(
        usage=Usage(
            input_tokens=2000,
            output_tokens=800,
            estimated_cost_usd=0.0,
            model_used=cfg.model,
        )
    )
    turn.items.append(UserMessageItem(text="hi"))
    enriched = enrich_usage(
        cfg,
        model_used=cfg.model,
        fallback_used=False,
        usage={"prompt_tokens": 2000, "completion_tokens": 800},
    )
    turn.usage.estimated_cost_usd = enriched["estimated_cost_usd"]
    thread.turns.append(turn)
    store.create_thread(thread)
    summary = thread_cost_summary(thread)
    assert summary["estimated_cost_usd"] > 0
