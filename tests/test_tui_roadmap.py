from __future__ import annotations

from pathlib import Path

from agent.tui.footer_state import FooterMode, FooterProps, format_footer, resolve_footer_mode
from agent.tui.formatting import context_bar, format_approval_label
from agent.tui.paste_burst import PasteBurstDetector
from agent.tui.statusline import StatuslineSettings, load_statusline_settings
from agent.tui.streaming_controller import AssistantStreamController
from agent.tui.table_holdback import table_holdback_suffix
from agent.threads_picker import thread_resume_preview
from agent.models import Thread, UserMessageItem, Turn


def test_footer_mode_user_input() -> None:
    mode = resolve_footer_mode(
        pending_approval=False,
        pending_user_input=True,
        turn_running=True,
        threads_loading=False,
        screen_mode="chat",
        reverse_search_active=False,
        resume_highlight=False,
    )
    assert mode == FooterMode.USER_INPUT


def test_footer_mode_approval() -> None:
    mode = resolve_footer_mode(
        pending_approval=True,
        pending_user_input=False,
        turn_running=False,
        threads_loading=False,
        screen_mode="chat",
        reverse_search_active=False,
        resume_highlight=False,
    )
    assert mode == FooterMode.APPROVAL
    text = format_footer(
        FooterProps(mode=mode, app_version="1.0", screen_mode="chat", terminal_width=100)
    )
    assert "y" in text


def test_paste_burst_detects_rapid_chars() -> None:
    det = PasteBurstDetector(enabled=True, burst_window_sec=0.2, min_burst_chars=4)
    out = []
    for ch in "hello!!!!":
        r = det.feed(ch)
        if r:
            out.append(r)
    joined = "".join(out)
    assert "hello" in joined


def test_table_holdback_suffix() -> None:
    md = "| a | b |\n|---|---|\n| 1 "
    assert table_holdback_suffix(md)
    done = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert table_holdback_suffix(done) == ""


def test_streaming_table_holdback() -> None:
    ctrl = AssistantStreamController()
    partial = "| h | h |\n|---|---|\n| v "
    assert ctrl.absorb(partial) == ""
    full = partial + "\n| v | v |"
    tail = ctrl.absorb(full)
    assert "| v | v |" in tail or tail == ""


def test_statusline_settings_from_toml(tmp_path: Path) -> None:
    cfg = tmp_path / ".agent-cli" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[tui]\nstatusline = \"model, branch, cwd\"\n", encoding="utf-8")
    settings = load_statusline_settings(cfg)
    assert settings.items == ["model", "branch", "cwd"]


def test_thread_resume_preview_last_user_message() -> None:
    thread = Thread(
        id="t1",
        cwd=".",
        model="m",
        turns=[Turn(items=[UserMessageItem(text="fix the login bug")])],
    )
    assert "login" in thread_resume_preview(thread)


def test_sandbox_danger_label() -> None:
    assert "awaiting" in format_approval_label(
        approval_mode="interactive", pending_approval=True
    )
    assert "green" in context_bar(used_pct=10)
