from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.events import EventEmitter
from agent.loop import run_turn
from agent.metrics import MetricsCollector
from agent.models import AgentMessageItem, Thread, new_id
from agent.multi_agent.budget_state import load_budget_state, save_budget_state
from agent.multi_agent.budgets import BUILTIN_PROFILES, SwarmBudgetTracker, profile_settings
from agent.multi_agent.registry import WorkerRegistry
from agent.settings import MultiAgentSettings, SwarmBudgetSettings
from agent.store import ThreadStore
from model.openrouter import CompletionResult


def test_profile_settings_strict() -> None:
    settings = profile_settings("strict")
    assert settings.enabled is True
    assert settings.max_workers_spawned == 5


def test_profile_settings_off() -> None:
    settings = profile_settings("off")
    assert settings.enabled is False


def test_profile_unknown_returns_base() -> None:
    base = SwarmBudgetSettings(max_workers_spawned=99)
    settings = profile_settings("unknown", base)
    assert settings.max_workers_spawned == 99


def test_wall_clock_budget_exceeded() -> None:
    settings = SwarmBudgetSettings(enabled=True, max_wall_clock_sec=1, on_budget_exceeded="kill")
    tracker = SwarmBudgetTracker(settings, started_at=time.monotonic() - 5)
    metric = tracker.tick_wall_clock()
    assert metric == "wall_clock_sec"
    assert tracker.should_kill() is True


def test_tool_call_budget_exceeded() -> None:
    settings = SwarmBudgetSettings(enabled=True, max_supervisor_tool_calls=1, on_budget_exceeded="kill")
    tracker = SwarmBudgetTracker(settings)
    metric = tracker.record_tool_call()
    assert metric == "supervisor_tool_calls"
    assert tracker.should_kill() is True


def test_worker_spawn_budget_exceeded() -> None:
    settings = SwarmBudgetSettings(enabled=True, max_workers_spawned=1, on_budget_exceeded="kill")
    tracker = SwarmBudgetTracker(settings)
    metric = tracker.record_worker_spawn()
    assert metric == "workers_spawned"
    assert tracker.should_kill() is True


def test_token_budget_exceeded() -> None:
    settings = SwarmBudgetSettings(
        enabled=True,
        max_openrouter_input_tokens=100,
        on_budget_exceeded="kill",
    )
    tracker = SwarmBudgetTracker(settings)
    metric = tracker.record_usage({"prompt_tokens": 150})
    assert metric == "input_tokens"
    assert tracker.should_kill() is True


def test_output_token_budget_exceeded() -> None:
    settings = SwarmBudgetSettings(
        enabled=True,
        max_openrouter_output_tokens=50,
        on_budget_exceeded="kill",
    )
    tracker = SwarmBudgetTracker(settings)
    metric = tracker.record_usage({"completion_tokens": 80})
    assert metric == "output_tokens"
    assert tracker.should_kill() is True


def test_cost_budget_exceeded() -> None:
    from agent.settings import SwarmBudgetPricing

    settings = SwarmBudgetSettings(
        enabled=True,
        max_openrouter_input_tokens=0,
        max_openrouter_output_tokens=0,
        max_estimated_cost_usd=0.001,
        on_budget_exceeded="kill",
        pricing={"test-model": SwarmBudgetPricing(input_per_million=3.0, output_per_million=15.0)},
    )
    tracker = SwarmBudgetTracker(settings, model="test-model")
    metric = tracker.record_usage({"prompt_tokens": 500_000, "completion_tokens": 0})
    assert metric == "estimated_cost_usd"
    assert tracker.should_kill() is True


def test_warn_mode_continues_once() -> None:
    MetricsCollector.reset_for_tests()
    settings = SwarmBudgetSettings(
        enabled=True,
        max_supervisor_tool_calls=2,
        on_budget_exceeded="warn",
    )
    tracker = SwarmBudgetTracker(settings)
    assert tracker.record_tool_call() is None
    assert tracker.record_tool_call() is None
    assert tracker.should_kill() is False
    assert tracker.record_tool_call() == "supervisor_tool_calls"
    assert tracker.snapshot.exceeded is True
    assert tracker.should_kill() is False
    assert MetricsCollector.global_collector().snapshot().labeled_counters["agent_swarm_budget_exceeded_total"]["supervisor_tool_calls"] >= 1


def test_budget_kill_cancels_workers(tmp_path: Path) -> None:
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
            max_workers_per_turn=5,
            worker_auto_approve=True,
            budgets=SwarmBudgetSettings(enabled=True, max_workers_spawned=1, on_budget_exceeded="kill"),
        ),
    )
    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "spawn_worker", "arguments": '{"task":"first"}'},
        },
    ]
    stream_results = [
        CompletionResult("", tool_calls, "tool_calls", None),
    ]
    events: list = []
    emitter = EventEmitter(lambda e: events.append(e))

    def slow_worker(self, parent_thread, turn_id, worker_id):
        rec = self._workers[worker_id]
        rec.status = "running"
        time.sleep(0.05)

    with patch("agent.loop.OpenRouterClient") as mock_client_cls, patch.object(
        WorkerRegistry, "_run_worker", slow_worker
    ):
        mock_client_cls.return_value.stream_completion.side_effect = stream_results
        turn = run_turn(parent, "delegate", config, store, events=emitter)

    assert turn.status == "failed"
    budget_events = [e for e in events if e.type == "multi_agent.budget.exceeded"]
    assert budget_events
    agent_msgs = [i for i in turn.items if isinstance(i, AgentMessageItem)]
    assert any("budget exceeded" in (m.text or "").lower() for m in agent_msgs)


def test_budget_state_persisted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.multi_agent.budget_state.budget_state_dir",
        lambda: tmp_path / "budgets",
    )
    save_budget_state("thread-1", {"workers_spawned": 2})
    loaded = load_budget_state("thread-1")
    assert loaded is not None
    assert loaded["workers_spawned"] == 2


def test_run_turn_saves_budget_on_completion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.multi_agent.budget_state.budget_state_dir",
        lambda: tmp_path / "budgets",
    )
    parent = Thread(id=new_id(), cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(parent)
    config = Config(
        cwd=tmp_path,
        model="test",
        openrouter_api_key="x",
        approval_mode="auto",
        multi_agent=MultiAgentSettings(enabled=True, worker_auto_approve=True),
    )
    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.stream_completion.return_value = CompletionResult(
            "done", [], "stop", None
        )
        run_turn(parent, "hi", config, store, events=EventEmitter(), budget_profile="standard")
    loaded = load_budget_state(parent.id)
    assert loaded is not None


def test_builtin_profiles_cover_expected_keys() -> None:
    assert set(BUILTIN_PROFILES) >= {"off", "standard", "strict"}
    assert BUILTIN_PROFILES["strict"]["max_workers_spawned"] < BUILTIN_PROFILES["standard"]["max_workers_spawned"]


def test_disabled_tracker_noops() -> None:
    tracker = SwarmBudgetTracker(SwarmBudgetSettings(enabled=False))
    assert tracker.record_tool_call() is None
    assert tracker.record_worker_spawn() is None
    assert tracker.tick_wall_clock() is None
