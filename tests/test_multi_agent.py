from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.events import EventEmitter
from agent.loop import run_turn
from agent.models import CollabWorkerItem, Thread, new_id
from agent.multi_agent.registry import WorkerRegistry
from agent.settings import MultiAgentSettings
from agent.store import ThreadStore
from model.openrouter import CompletionResult


def test_spawn_concurrency_cap(tmp_path: Path) -> None:
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="test",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(
            enabled=True,
            max_workers_per_turn=1,
            worker_auto_approve=True,
        ),
    )

    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "spawn_worker", "arguments": '{"task":"first"}'},
        },
        {
            "id": "call-2",
            "type": "function",
            "function": {"name": "spawn_worker", "arguments": '{"task":"second"}'},
        },
    ]
    stream_results = [
        CompletionResult("", tool_calls, "tool_calls", None),
        CompletionResult("All workers done.", [], "stop", None),
    ]

    def fast_worker(self, parent_thread, turn_id, worker_id):
        rec = self._workers[worker_id]
        rec.status = "completed"
        rec.summary = "done"
        rec.worker_thread_id = new_id()
        rec._done.set()
        with self._lock:
            self._running = max(0, self._running - 1)

    with patch("agent.loop.OpenRouterClient") as mock_client_cls, patch.object(
        WorkerRegistry, "_run_worker", fast_worker
    ):
        mock_client_cls.return_value.stream_completion.side_effect = stream_results
        run_turn(
            parent,
            "delegate work",
            config,
            store,
            events=EventEmitter(),
        )

    loaded = store.load_thread(parent.id)
    spawn_items = [
        i
        for t in loaded.turns
        for i in t.items
        if isinstance(i, CollabWorkerItem)
    ]
    assert len(spawn_items) == 2
    assert spawn_items[0].status in ("queued", "completed", "running")
    assert spawn_items[1].status == "failed"
    assert "limit reached" in (spawn_items[1].summary or "").lower()


def test_spawn_worker_mocked_run(tmp_path: Path) -> None:
    from agent.multi_agent.spawn import spawn_worker

    parent = Thread(id="parent-id-12345678", cwd=str(tmp_path), model="test")
    from agent.models import AgentMessageItem, CollabSpawnItem, Turn

    turn = Turn()
    turn.items = [AgentMessageItem(text="prior work")]
    parent.turns.append(turn)

    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)

    config = Config(
        cwd=tmp_path,
        model="test",
        openrouter_api_key="x",
        multi_agent=MultiAgentSettings(enabled=True, worker_auto_approve=True),
    )

    worker_turn = Turn(status="completed")
    worker_turn.items = [AgentMessageItem(text="Worker finished: tests pass.")]

    def fake_run(wt, prompt, cfg, st, **kwargs):
        wt.turns.append(worker_turn)
        return worker_turn

    text, item = spawn_worker(
        parent,
        {"task": "run pytest", "title": "pytest worker"},
        config,
        store,
        run_turn_fn=fake_run,
    )
    assert "Worker finished" in text
    assert isinstance(item, CollabSpawnItem)
    assert item.status == "completed"
    assert item.worker_thread_id
    assert item.worker_thread_id != parent.id


def test_spawn_worker_failure(tmp_path: Path) -> None:
    from agent.multi_agent.spawn import spawn_worker
    from agent.models import CollabSpawnItem

    parent = Thread(id="parent-fail-12345678", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="test",
        openrouter_api_key="x",
        multi_agent=MultiAgentSettings(enabled=True),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("worker exploded")

    text, item = spawn_worker(
        parent,
        {"task": "fail"},
        config,
        store,
        run_turn_fn=boom,
    )
    assert item.status == "failed"
    assert "worker exploded" in item.summary or "exploded" in text
