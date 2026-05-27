from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.tui.footer_state import FooterMode, FooterProps, format_footer
from agent.tui.status_row import StatusRow
from agent.tui.statusline import load_tui_settings


def test_load_tui_settings_reduced_motion_from_toml(tmp_path: Path) -> None:
    cfg = tmp_path / ".agent-cli" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[tui]\nreduced_motion = true\n", encoding="utf-8")
    settings = load_tui_settings(cfg)
    assert settings.reduced_motion is True


def test_load_tui_settings_reduced_motion_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_TUI_REDUCED_MOTION", "1")
    assert load_tui_settings(cfg).reduced_motion is True


def test_footer_working_without_spinner_when_reduced_motion() -> None:
    text = format_footer(
        FooterProps(
            mode=FooterMode.WORKING,
            app_version="1.0",
            screen_mode="chat",
            terminal_width=100,
            reduced_motion=True,
        )
    )
    assert "⠋" not in text
    assert "working" in text


def test_status_row_reduced_motion_static_indicator() -> None:
    row = StatusRow(MagicMock(), reduced_motion=True)
    row.start_turn()
    row._update()
    markup = row._widget.update.call_args[0][0]
    assert "●" in markup
    assert "⠋" not in markup
