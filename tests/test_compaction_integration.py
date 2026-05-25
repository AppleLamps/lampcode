from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.compaction import compact_thread_if_needed
from agent.config import Config
from agent.models import AgentMessageItem, Thread, Turn, UserMessageItem
from agent.settings import CompactionSettings
from agent.store import ThreadStore


def test_compaction_preserves_recent_turns(tmp_path: Path) -> None:
    thread = Thread(id="t1", cwd=str(tmp_path), model="test")
    for i in range(4):
        turn = Turn(status="completed")
        turn.items = [
            UserMessageItem(text=f"question {i}"),
            AgentMessageItem(text=f"answer {i}" * 500),
        ]
        thread.turns.append(turn)

    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=100,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.1, keep_recent_turns=2),
    )
    client = MagicMock()
    client.complete.return_value = "Summary of older work."

    result = compact_thread_if_needed(thread, config, store, client)
    assert result.performed
    assert result.removed_items > 0
    assert len(thread.turns) == 3  # 1 compact + 2 recent
    assert thread.turns[0].items[0].type == "contextCompaction"
    assert "[Compaction Summary]" in thread.turns[0].items[1].text
