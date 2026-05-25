import json
from pathlib import Path

import pytest

from agent.compaction import should_compact
from agent.config import Config
from agent.context import estimate_tokens


def test_should_compact_at_threshold() -> None:
    config = Config(
        cwd=Path("."),
        model="test",
        context_window_tokens=1000,
        compaction_threshold=0.7,
        openrouter_api_key="x",
    )
    messages = [{"role": "user", "content": "x" * 3000}]
    assert estimate_tokens(messages) >= 700
    assert should_compact(messages, config)


def test_should_not_compact_below_threshold() -> None:
    config = Config(
        cwd=Path("."),
        model="test",
        context_window_tokens=100_000,
        compaction_threshold=0.7,
        openrouter_api_key="x",
    )
    messages = [{"role": "user", "content": "short"}]
    assert not should_compact(messages, config)
