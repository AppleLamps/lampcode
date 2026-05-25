from __future__ import annotations

import platform
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent.compaction import (
    COMPACTION_PROMPT,
    compact_thread_if_needed,
    count_thread_compactions,
)
from agent.config import Config
from agent.context import build_thread_messages
from agent.execution.shell_session import ShellSession
from agent.hooks.runner import HooksRunner
from agent.models import AgentMessageItem, ContextCompactionItem, Thread, Turn, UserMessageItem
from agent.sandbox.enforcer import check_run_command
from agent.sandbox.policy import SandboxMode
from agent.session import HarnessSession
from agent.settings import CompactionSettings, ShellSettings
from agent.store import ThreadStore
from agent.tool_round import can_parallelize_tool_round, run_parallel_tool_dispatches
from approval.cache import ApprovalCache
from approval.gate import needs_approval_prompt, prompt_approval
from cli.main import app

runner = CliRunner()

TASK_MARKER = "TASK_MARKER=phase24-double-compact"


def test_exec_session_stdin_and_output_cap(tmp_path: Path) -> None:
    settings = ShellSettings(enabled=True, persistent=True, max_output_chars=500)
    sess = ShellSession("exec-v1", tmp_path, settings)
    try:
        if platform.system() == "Windows":
            sess.run("set AGENT_MARK=phase24")
            result = sess.run("echo %AGENT_MARK%")
        else:
            sess.run("export AGENT_MARK=phase24")
            result = sess.run("echo $AGENT_MARK")
        assert result.meta["persistent"] is True
        assert "phase24" in result.output
    finally:
        sess.close()


def test_exec_truncation_marker_in_output(tmp_path: Path) -> None:
    settings = ShellSettings(enabled=True, persistent=True, max_output_chars=20)
    sess = ShellSession("truncate", tmp_path, settings)
    try:
        if platform.system() == "Windows":
            result = sess.run("echo hello-world-truncation-test")
        else:
            result = sess.run("echo hello-world-truncation-test")
        assert result.meta.get("truncated") is True
        assert "[... output truncated ...]" in result.output
    finally:
        sess.close()


def test_approval_cache_skips_second_identical_command() -> None:
    session = HarnessSession()
    session.approval_cache.record(
        "run_command",
        {"cmd": "git status --short"},
    )
    config = Config.resolve(skip_git_check=True)
    assert not needs_approval_prompt(
        "run_command",
        {"cmd": "git status --short"},
        config,
        session=session,
    )


def test_sandbox_retry_after_session_escalation() -> None:
    session = HarnessSession()
    cfg = Config.resolve(skip_git_check=True)
    cfg.sandbox_mode = SandboxMode.WORKSPACE_WRITE
    blocked = check_run_command("curl https://example.com", cfg.cwd, cfg.sandbox_mode, session=session)
    assert blocked.blocked
    assert blocked.retryable
    session.grant_permission("network", duration="turn")
    allowed = check_run_command("curl https://example.com", cfg.cwd, cfg.sandbox_mode, session=session)
    assert allowed.allowed


def test_compaction_includes_project_rules_in_summary_prompt(tmp_path: Path) -> None:
    rules_path = tmp_path / "AGENTS.md"
    rules_path.write_text("Always run pytest after edits.", encoding="utf-8")
    thread = Thread(id="t-rules", cwd=str(tmp_path), model="test")
    thread.turns.append(
        Turn(
            status="completed",
            items=[
                UserMessageItem(text="Fix the bug"),
                AgentMessageItem(text="Looking at tests." * 300),
            ],
        )
    )
    for i in range(3):
        thread.turns.append(
            Turn(
                status="completed",
                items=[
                    UserMessageItem(text=f"follow {i}"),
                    AgentMessageItem(text="detail" * 400),
                ],
            )
        )
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=100,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.1, keep_recent_turns=1),
    )
    client = MagicMock()
    client.complete.side_effect = lambda messages: (
        messages[0]["content"] if messages[0]["role"] == "system" else "summary"
    )

    compact_thread_if_needed(thread, config, store, client)
    system_prompt = client.complete.call_args[0][0][0]["content"]
    assert "Always run pytest" in system_prompt
    assert COMPACTION_PROMPT in system_prompt


def test_double_compaction_preserves_task_marker(tmp_path: Path) -> None:
    thread = Thread(id="t-double", cwd=str(tmp_path), model="test")
    thread.turns.append(
        Turn(
            status="completed",
            items=[
                UserMessageItem(text=f"Fix bug. {TASK_MARKER}"),
                AgentMessageItem(text="step one" * 400),
            ],
        )
    )
    for i in range(4):
        thread.turns.append(
            Turn(
                status="completed",
                items=[
                    UserMessageItem(text=f"q{i}"),
                    AgentMessageItem(text="a" * 400),
                ],
            )
        )
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=80,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.05, keep_recent_turns=1),
    )
    client = MagicMock()
    client.complete.return_value = f"Preserve {TASK_MARKER} in summary."

    first = compact_thread_if_needed(thread, config, store, client)
    assert first.performed
    for i in range(4):
        thread.turns.append(
            Turn(
                status="completed",
                items=[
                    UserMessageItem(text=f"more {i}"),
                    AgentMessageItem(text="b" * 400),
                ],
            )
        )
    second = compact_thread_if_needed(thread, config, store, client)
    assert second.performed
    assert count_thread_compactions(thread) == 2
    joined = "\n".join(
        m["content"] for m in build_thread_messages(thread) if isinstance(m.get("content"), str)
    )
    assert TASK_MARKER in joined


def test_compaction_warning_after_second_compact(tmp_path: Path) -> None:
    thread = Thread(id="t-warn", cwd=str(tmp_path), model="test")
    thread.turns = [
        Turn(
            status="completed",
            items=[
                ContextCompactionItem(summarized_items=1),
                AgentMessageItem(text="[Compaction Summary]\nfirst"),
            ],
        ),
        Turn(
            status="completed",
            items=[UserMessageItem(text="x"), AgentMessageItem(text="y" * 500)],
        ),
        Turn(
            status="completed",
            items=[UserMessageItem(text="z"), AgentMessageItem(text="w" * 500)],
        ),
    ]
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=50,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.05, keep_recent_turns=1),
    )
    client = MagicMock()
    client.complete.return_value = "summary"
    result = compact_thread_if_needed(thread, config, store, client)
    assert result.warning is not None
    assert "multiple compactions" in result.warning.lower()


def test_parallel_read_files_preserves_order() -> None:
    order: list[int] = []
    lock_start = time.monotonic()

    def make_job(index: int):
        def _run() -> str:
            order.append(index)
            time.sleep(0.05)
            return f"out-{index}"

        return _run

    jobs = [(i, make_job(i)) for i in range(3)]
    results = run_parallel_tool_dispatches(jobs, max_workers=3)
    assert [text for _, text in results] == ["out-0", "out-1", "out-2"]
    assert len(order) == 3
    assert time.monotonic() - lock_start < 0.2


def test_patch_and_read_not_parallelized() -> None:
    assert not can_parallelize_tool_round(["read_file", "apply_patch"])


def test_compact_hooks_fire(tmp_path: Path) -> None:
    hooks_file = tmp_path / ".agent-cli" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(
        '{"on_pre_compact": [{"command": "echo pre", "timeout_sec": 5}], '
        '"on_post_compact": [{"command": "echo post", "timeout_sec": 5}]}',
        encoding="utf-8",
    )
    thread = Thread(id="t-hook", cwd=str(tmp_path), model="test")
    for i in range(4):
        thread.turns.append(
            Turn(
                status="completed",
                items=[
                    UserMessageItem(text=f"q{i}"),
                    AgentMessageItem(text="long" * 300),
                ],
            )
        )
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=80,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.05, keep_recent_turns=1),
    )
    client = MagicMock()
    client.complete.return_value = "summary"
    hooks = HooksRunner.from_cwd(tmp_path)
    with patch.object(hooks, "run", wraps=hooks.run) as mock_run:
        compact_thread_if_needed(thread, config, store, client, hooks_runner=hooks)
        events = [call.args[0] for call in mock_run.call_args_list]
    assert "on_pre_compact" in events
    assert "on_post_compact" in events
