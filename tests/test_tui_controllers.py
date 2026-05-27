"""Smoke tests for mode controller extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tui.app import AgentTuiApp
from agent.tui.controllers import ChatController, HomeController, TurnController


@pytest.fixture
def tui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentTuiApp:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return AgentTuiApp(cwd=tmp_path)


def test_app_wires_mode_controllers(tui_app: AgentTuiApp) -> None:
    assert isinstance(tui_app._home, HomeController)
    assert isinstance(tui_app._chat, ChatController)
    assert isinstance(tui_app._turn, TurnController)
    assert tui_app._home._app is tui_app
    assert tui_app._chat._app is tui_app
    assert tui_app._turn._app is tui_app


def test_refresh_chrome_delegates_to_chat(tui_app: AgentTuiApp) -> None:
    called = []

    def _track(**kwargs):
        called.append(kwargs)

    tui_app._chat.refresh_chrome = _track  # type: ignore[method-assign]
    tui_app._refresh_chrome(include_context=False)
    assert called == [{"include_context": False}]


def test_set_mode_updates_mode_field(tui_app: AgentTuiApp) -> None:
    """Mode field is owned by HomeController.set_mode (DOM requires run_test)."""
    tui_app._mode = "home"

    def _set_mode_no_dom(mode: str) -> None:
        tui_app._mode = mode

    tui_app._home.set_mode = _set_mode_no_dom  # type: ignore[method-assign]
    tui_app._set_mode("resume")
    assert tui_app._mode == "resume"
