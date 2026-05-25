from pathlib import Path
from unittest.mock import patch

import pytest

from agent.cancel import CancelToken, CancelledError
from agent.config import Config
from agent.events import EventEmitter
from agent.loop import run_turn
from agent.models import Thread
from agent.store import ThreadStore
from model.openrouter import CompletionResult


def test_cancel_during_stream(tmp_path: Path) -> None:
    thread = Thread(id="t1", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    cancel = CancelToken()

    def fake_stream(*args, **kwargs):
        cancel.cancel()
        cancel.check()

    with patch("agent.loop.OpenRouterClient") as mock_client:
        mock_client.return_value.stream_completion.side_effect = fake_stream
        with pytest.raises(CancelledError):
            run_turn(
                thread,
                "hello",
                config,
                store,
                cancel_token=cancel,
                events=EventEmitter(),
            )

    loaded = store.load_thread(thread.id)
    assert loaded.turns[-1].status == "cancelled"
    assert any(i.text == "Turn cancelled by user." for i in loaded.turns[-1].items)
