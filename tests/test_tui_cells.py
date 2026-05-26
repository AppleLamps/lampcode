"""Tests for TUI cell renderers."""

from __future__ import annotations

from pathlib import Path

from agent.tui.cells import render_cell
from agent.tui.cells.base import (
    ApprovalCell,
    AssistantMessageCell,
    CompactionCell,
    ErrorCell,
    FileChange,
    PatchCell,
    PlanCell,
    ReasoningCell,
    SystemCell,
    ToolExecCell,
    TurnSummaryCell,
    UserMessageCell,
)
from agent.tui.cells.error import render_approval_banner_text
from agent.tui.diff_render import strip_rich_markup

_GOLDEN_DIR = Path(__file__).parent / "golden" / "tui"


def _plain(cell) -> str:
    rendered = render_cell(cell)
    if isinstance(rendered, str):
        return strip_rich_markup(rendered)
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, width=200, force_terminal=True).print(rendered)
    return buf.getvalue()


def _assert_golden(name: str, rendered: str) -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    golden_file = _GOLDEN_DIR / name
    if not golden_file.exists():
        golden_file.write_text(rendered, encoding="utf-8")
    assert rendered == golden_file.read_text(encoding="utf-8")


def test_render_user_message() -> None:
    text = _plain(UserMessageCell(text="hello world"))
    assert "You" in text
    assert "hello world" in text


def test_render_assistant_message() -> None:
    text = _plain(AssistantMessageCell(text="Here is `code`"))
    assert "code" in text


def test_render_tool_exec() -> None:
    cell = ToolExecCell(
        tool_name="run_command",
        args_brief="pytest -q",
        status="completed",
        output="1 passed\n",
        exit_code=0,
    )
    text = _plain(cell)
    assert "run_command" in text
    assert "pytest -q" in text
    assert "1 passed" in text


def test_render_patch_cell() -> None:
    cell = PatchCell(
        files=[FileChange(path="foo.py", change_type="update")],
        diff_text="+added\n-removed",
        status="completed",
    )
    text = _plain(cell)
    assert "apply_patch" in text
    assert "foo.py" in text
    assert "+added" in text


def test_render_plan_cell() -> None:
    cell = PlanCell(summary="Fix bug", body="Step 1: investigate")
    text = _plain(cell)
    assert "Plan" in text
    assert "Fix bug" in text


def test_render_compaction_cell() -> None:
    cell = CompactionCell(kind="completed", removed_items=5)
    text = _plain(cell)
    assert "Compacted" in text
    assert "5" in text


def test_render_approval_cell() -> None:
    cell = ApprovalCell(summary="run_command: pytest", tool_name="run_command")
    text = render_cell(cell)
    assert "pytest" in text
    assert "y/n/a/A" in text


def test_render_error_cell() -> None:
    cell = ErrorCell(message="Something failed", severity="error")
    text = _plain(cell)
    assert "Something failed" in text


def test_render_turn_summary() -> None:
    cell = TurnSummaryCell(
        status="completed",
        model="claude-sonnet",
        cost=0.002,
        files_changed=2,
    )
    text = render_cell(cell)
    assert "claude-sonnet" in text
    assert "$0.002" in text
    assert "2 files" in text


def test_render_reasoning_cell_collapsed() -> None:
    cell = ReasoningCell(text="line1\nline2\nline3", expanded=False)
    text = _plain(cell)
    assert "Thinking" in text
    assert "3 lines" in text


def test_render_system_cell() -> None:
    text = _plain(SystemCell(text="sync done"))
    assert "sync done" in text


def test_golden_user_message() -> None:
    _assert_golden("user_message.txt", _plain(UserMessageCell(text="Fix the login bug")))


def test_golden_patch_cell() -> None:
    _assert_golden(
        "patch_cell.txt",
        _plain(
            PatchCell(
                files=[FileChange(path="auth.py", change_type="update")],
                diff_text="+def login(): pass\n-return None",
                status="completed",
            )
        ),
    )


def test_golden_exec_cell() -> None:
    _assert_golden(
        "exec_cell.txt",
        _plain(
            ToolExecCell(
                tool_name="run_command",
                args_brief="pytest -q",
                status="completed",
                output="1 passed",
                exit_code=0,
            )
        ),
    )


def test_golden_approval_cell() -> None:
    # Keep Rich markup — strip_rich_markup would remove [y/n/a/A] as a false tag
    _assert_golden(
        "approval_cell.txt",
        render_cell(
            ApprovalCell(
                summary="run_command: pytest -q",
                tool_name="run_command",
            )
        ).strip(),
    )


def test_golden_approval_banner() -> None:
    _assert_golden(
        "approval_banner.txt",
        strip_rich_markup(
            render_approval_banner_text(
                "run_command: pytest -q",
                diff_preview="+def fix(): pass\n-return None",
            )
        ),
    )


def test_golden_error_cell() -> None:
    _assert_golden(
        "error_cell.txt",
        _plain(
            ErrorCell(
                message="sandbox blocked (workspace-write): command not allowed",
                severity="warning",
                command="rm -rf /",
            )
        ),
    )


def test_golden_turn_summary() -> None:
    _assert_golden(
        "turn_summary.txt",
        strip_rich_markup(
            render_cell(
                TurnSummaryCell(
                    status="completed",
                    model="claude-sonnet-4",
                    cost=0.002,
                    files_changed=2,
                    lines_added=12,
                    lines_removed=3,
                    commands_run=1,
                )
            )
        ),
    )


def test_golden_streaming_assistant() -> None:
    _assert_golden(
        "streaming_assistant.txt",
        _plain(
            AssistantMessageCell(
                text="update login handler",
                streaming=True,
            )
        ),
    )
