from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Config
from agent.models import Thread, Turn, UserMessageItem
from agent.tui.context_usage import compute_context_usage
from agent.tui.slash_commands import (
    TuiSlashState,
    execute_slash_command,
    parse_slash_command,
)
from agent.store import ThreadStore


def test_parse_slash_command() -> None:
    assert parse_slash_command("/model openrouter/owl-alpha") == (
        "/model",
        "openrouter/owl-alpha",
    )
    assert parse_slash_command("hello") is None


def test_slash_model_and_plan() -> None:
    state = TuiSlashState()
    cfg = Config(cwd=Path("."), model="openrouter/owl-alpha", openrouter_api_key="x")
    store = ThreadStore()

    model_result = execute_slash_command(
        "/model", "anthropic/claude-sonnet-4", state=state, config=cfg, store=store, thread=None
    )
    assert model_result.message.startswith("Model set")
    assert state.model_override == "anthropic/claude-sonnet-4"

    plan_on = execute_slash_command(
        "/plan", "on", state=state, config=cfg, store=store, thread=None
    )
    assert state.plan_mode is True
    assert "Plan mode ON" in plan_on.message

    plan_status = execute_slash_command(
        "/plan", "", state=state, config=cfg, store=store, thread=None
    )
    assert "Plan mode: on" in plan_status.message


def test_context_usage_empty_thread(tmp_path: Path) -> None:
    cfg = Config.resolve(cwd=tmp_path, context_window_tokens=1000)
    usage = compute_context_usage(cfg, None)
    assert usage.used_pct == 0
    assert usage.left_pct == 100
    assert usage.footer_label() == "Context 100% left · 0% used"


def test_context_usage_with_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = Config.resolve(cwd=tmp_path, context_window_tokens=100)
    thread = Thread(id="t1", cwd=str(tmp_path), model=cfg.model)
    turn = Turn()
    turn.items.append(UserMessageItem(text="x" * 400))
    thread.turns.append(turn)
    monkeypatch.setattr(
        "agent.tui.context_usage.resolve_context_window_tokens",
        lambda _cfg: 100,
    )
    usage = compute_context_usage(cfg, thread)
    assert usage.used_pct > 0
    assert usage.left_pct < 100
    assert "Context" in usage.footer_label()
