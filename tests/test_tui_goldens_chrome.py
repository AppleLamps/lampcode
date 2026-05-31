"""Golden snapshots for footer modes, composer chrome, streaming holdback, tool groups."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from agent.config import Config
from agent.context_meter import ContextSnapshot
from agent.events import AgentEvent
from agent.models import Thread, Turn, UserMessageItem, utc_now_iso
from agent.tui.cells import render_cell
from agent.tui.cells.base import ToolGroupCell
from agent.tui.cells.message import assistant_message_visual
from agent.tui.composer_chrome import format_composer_meta
from agent.tui.diff_render import strip_rich_markup
from agent.tui.footer_state import FooterMode, FooterProps, format_footer, resolve_footer_mode
from agent.tui.statusline import StatuslineSettings
from agent.tui.streaming_controller import AssistantStreamController
from agent.tui.view_model import TuiState, apply_event_to_state

_GOLDEN_DIR = Path(__file__).parent / "golden" / "tui"


def _assert_golden(name: str, rendered: str) -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    golden_file = _GOLDEN_DIR / name
    if not golden_file.exists():
        golden_file.write_text(rendered, encoding="utf-8")
    assert rendered.rstrip("\n") == golden_file.read_text(encoding="utf-8").rstrip("\n")


def _plain_renderable(renderable, *, width: int = 120) -> str:
    buf = StringIO()
    Console(file=buf, width=width, force_terminal=True).print(renderable)
    return strip_rich_markup(buf.getvalue())


def _fixture_config() -> Config:
    return Config(cwd=Path("."), model="openrouter/owl-alpha", openrouter_api_key="test-key")


def _fixture_thread() -> Thread:
    return Thread(
        id="t-golden",
        cwd=".",
        model="openrouter/owl-alpha",
        created_at=utc_now_iso(),
        turns=[Turn(items=[UserMessageItem(text="fix login")])],
    )


def _fixture_context_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        window_tokens=128_000,
        baseline_tokens=8_000,
        headroom_tokens=4_000,
        effective_window=124_000,
        estimated_total=42_000,
        api_context=40_000,
        used_tokens=42_000,
        used_pct=33,
        left_pct=67,
    )


def _footer_golden(name: str, mode: FooterMode, *, width: int, reduced_motion: bool = False) -> None:
    text = format_footer(
        FooterProps(
            mode=mode,
            app_version="2.9.5",
            screen_mode="chat",
            turn_running=mode == FooterMode.WORKING,
            terminal_width=width,
            reduced_motion=reduced_motion,
            reverse_search_query="fix" if mode == FooterMode.REVERSE_SEARCH else "",
            reverse_search_match="fix login bug" if mode == FooterMode.REVERSE_SEARCH else "",
        )
    )
    _assert_golden(name, strip_rich_markup(text))


def _composer_meta_golden(name: str, **kwargs) -> None:
    settings = StatuslineSettings(
        items=["model", "mode", "sandbox", "approvals", "context", "session", "turns"]
    )
    text = format_composer_meta(
        config=_fixture_config(),
        model_short="owl-alpha",
        profile="interactive",
        plan_mode=kwargs.get("plan_mode", False),
        thread=kwargs.get("thread", _fixture_thread()),
        include_context=kwargs.get("include_context", True),
        turn_running=kwargs.get("turn_running", False),
        pending_approval=kwargs.get("pending_approval", False),
        session_auto_approve=kwargs.get("session_auto_approve", False),
        statusline_settings=settings,
        context_snapshot=_fixture_context_snapshot()
        if kwargs.get("include_context", True)
        else None,
    )
    _assert_golden(name, strip_rich_markup(text))


def test_golden_footer_approval_80() -> None:
    _footer_golden("footer_approval_80.txt", FooterMode.APPROVAL, width=80)


def test_golden_footer_approval_120() -> None:
    _footer_golden("footer_approval_120.txt", FooterMode.APPROVAL, width=120)


def test_golden_footer_working_80() -> None:
    _footer_golden("footer_working_80.txt", FooterMode.WORKING, width=80)


def test_golden_footer_working_reduced_motion_80() -> None:
    _footer_golden(
        "footer_working_reduced_motion_80.txt",
        FooterMode.WORKING,
        width=80,
        reduced_motion=True,
    )


def test_golden_footer_user_input_80() -> None:
    _footer_golden("footer_user_input_80.txt", FooterMode.USER_INPUT, width=80)


def test_golden_footer_reverse_search_80() -> None:
    _footer_golden("footer_reverse_search_80.txt", FooterMode.REVERSE_SEARCH, width=80)


def test_golden_composer_meta_idle() -> None:
    _composer_meta_golden("composer_meta_idle.txt")


def test_golden_composer_meta_working() -> None:
    _composer_meta_golden("composer_meta_working.txt", turn_running=True)


def test_golden_composer_meta_approval() -> None:
    _composer_meta_golden(
        "composer_meta_approval.txt",
        pending_approval=True,
        turn_running=False,
    )


def test_golden_tool_group_reads() -> None:
    cell = ToolGroupCell(
        tool_names=["read_file", "read_file", "read_file"],
        args_briefs=["src/a.py", "src/b.py", "src/c.py"],
        status="completed",
        expanded=True,
    )
    _assert_golden("tool_group_reads.txt", strip_rich_markup(render_cell(cell)))


def test_golden_streaming_partial_table_holdback() -> None:
    """While a markdown table streams, holdback keeps the live region minimal."""
    partial = "| Col A | Col B |\n|---|---|\n| 1 "
    stream = AssistantStreamController()
    visible = stream.absorb(partial)
    plain = _plain_renderable(assistant_message_visual(visible))
    _assert_golden("streaming_partial_table_holdback.txt", plain)


def test_golden_streaming_table_complete() -> None:
    complete = "| Col A | Col B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    stream = AssistantStreamController()
    visible = stream.absorb(complete)
    plain = _plain_renderable(assistant_message_visual(visible))
    _assert_golden("streaming_table_complete.txt", plain)


def test_resolve_footer_modes_matrix() -> None:
    assert (
        resolve_footer_mode(
            pending_approval=True,
            pending_user_input=False,
            turn_running=True,
            threads_loading=False,
            screen_mode="chat",
            reverse_search_active=False,
            resume_highlight=False,
        )
        == FooterMode.APPROVAL
    )
    assert (
        resolve_footer_mode(
            pending_approval=False,
            pending_user_input=True,
            turn_running=False,
            threads_loading=False,
            screen_mode="chat",
            reverse_search_active=False,
            resume_highlight=False,
        )
        == FooterMode.USER_INPUT
    )


def test_grouped_reads_from_events() -> None:
    state = TuiState()
    for path in ("a.py", "b.py", "c.py"):
        state = apply_event_to_state(
            state,
            AgentEvent(
                "tool.pending",
                thread_id="t",
                turn_id="u",
                data={"tool_name": "read_file", "arguments": {"path": path}},
            ),
        )
    assert any(isinstance(c, ToolGroupCell) for c in state.transcript)
