from __future__ import annotations

from pathlib import Path

from agent.config import Config
from agent.models import Thread, Turn, UserMessageItem, utc_now_iso
from agent.tui.composer_chrome import format_composer_meta, format_mode_badge
from agent.tui.statusline import StatuslineSettings
from agent.tui.footer_state import FooterMode, FooterProps, format_footer
from agent.tui.formatting import (
    context_bar,
    format_approval_label,
    truncate_session_title,
)
from agent.tui.context_usage import format_token_count


def test_context_bar_colors_by_usage() -> None:
    low = context_bar(used_pct=10)
    assert "green" in low
    high = context_bar(used_pct=95)
    assert "red" in high


def test_format_mode_badge_plan_vs_code() -> None:
    assert "PLAN" in format_mode_badge(plan_mode=True)
    assert "CODE" in format_mode_badge(plan_mode=False)


def test_format_composer_meta_includes_model_and_sandbox() -> None:
    config = Config(cwd=Path("."), model="minimax/minimax-m2.7", openrouter_api_key="x")
    settings = StatuslineSettings(items=["model", "profile", "sandbox", "approvals"])
    text = format_composer_meta(
        config=config,
        model_short="minimax-m2.7",
        profile="deep",
        plan_mode=False,
        thread=None,
        include_context=False,
        statusline_settings=settings,
    )
    assert "minimax-m2.7" in text
    assert "deep" in text
    assert "sandbox" in text
    assert "approvals" in text


def test_format_composer_meta_with_thread_turns() -> None:
    config = Config(cwd=Path("."), model="openrouter/owl-alpha", openrouter_api_key="x")
    thread = Thread(
        id="t1",
        cwd=".",
        model="openrouter/owl-alpha",
        created_at=utc_now_iso(),
        turns=[Turn(items=[UserMessageItem(text="hi")])],
    )
    text = format_composer_meta(
        config=config,
        model_short="owl",
        profile=None,
        plan_mode=True,
        thread=thread,
        include_context=True,
    )
    assert "plan" in text
    assert "turns" in text
    assert "context" in text
    assert "session" in text


def test_format_token_count() -> None:
    assert format_token_count(500) == "500"
    assert format_token_count(12_400) == "12k"
    assert format_token_count(1_500) == "1.5k"
    assert format_token_count(128_000) == "128k"


def test_format_approval_label() -> None:
    assert "auto" in format_approval_label(approval_mode="auto")
    assert "prompt" in format_approval_label(approval_mode="interactive")
    assert "awaiting" in format_approval_label(
        approval_mode="interactive", pending_approval=True
    )


def test_truncate_session_title() -> None:
    assert truncate_session_title("short") == "short"
    long = "x" * 80
    assert truncate_session_title(long).endswith("…")


def test_format_footer_modes() -> None:
    home = format_footer(
        FooterProps(mode=FooterMode.IDLE, app_version="1.0", screen_mode="home", terminal_width=120)
    )
    assert "Ctrl+S" in home
    working = format_footer(
        FooterProps(
            mode=FooterMode.WORKING,
            app_version="1.0",
            screen_mode="chat",
            turn_running=True,
            terminal_width=120,
        )
    )
    assert "Ctrl+C" in working
    chat = format_footer(
        FooterProps(mode=FooterMode.IDLE, app_version="1.0", screen_mode="chat", terminal_width=120)
    )
    assert "Shift+Enter" in chat
