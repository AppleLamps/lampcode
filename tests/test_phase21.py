from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from agent.config import Config
from agent.export.pr_description import build_pr_description, summarize_thread_for_pr
from agent.loop import run_turn
from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    Thread,
    Turn,
    UserMessageItem,
    new_id,
)
from agent.output_handler import OutputHandler
from agent.providers.openrouter import OpenRouterHealth, check_openrouter_health
from agent.repl_completer import REPL_COMMANDS, install_repl_completer
from agent.turn_checkpoint import TurnCheckpoint, TurnCheckpointStore, save_turn_checkpoint
from cli.main import app
from tools.patch import format_patch_brief, format_patch_preview_block, preview_patch

runner = CliRunner()

UPDATE_PATCH = """*** Begin Patch
*** Update File: calc.py
@@
 def add(a, b):
-    return a - b
+    return a + b
*** End Patch
"""


def test_check_openrouter_health_ok() -> None:
    resp = MagicMock(status_code=200)
    health = check_openrouter_health("key", http_get=lambda *a, **k: resp)
    assert health.reachability == "ok"
    assert health.key_validity == "ok"


def test_check_openrouter_health_invalid_key() -> None:
    resp = MagicMock(status_code=401)
    health = check_openrouter_health("bad", http_get=lambda *a, **k: resp)
    assert health.reachability == "ok"
    assert health.key_validity == "invalid"


def test_check_openrouter_health_unreachable() -> None:
    def boom(*a, **k):
        raise OSError("offline")

    health = check_openrouter_health("k", http_get=boom)
    assert health.reachability == "unreachable"


def test_preview_patch_stats() -> None:
    previews, err = preview_patch(UPDATE_PATCH)
    assert err is None
    assert len(previews) == 1
    assert previews[0].path.startswith("calc.py")
    assert previews[0].additions >= 1
    assert previews[0].deletions >= 1


def test_format_patch_brief_lists_files() -> None:
    brief = format_patch_brief(UPDATE_PATCH)
    assert "calc.py" in brief
    assert "apply_patch:" in brief


def test_format_patch_brief_begin_patch_with_path_suffix() -> None:
    """Models sometimes put the path on the Begin Patch line (unified diff body)."""
    patch = """*** Begin Patch src/styles/base.css
--- a/src/styles/base.css
+++ b/src/styles/base.css
@@ -1,1 +1,1 @@
-old
+new
*** End Patch
"""
    brief = format_patch_brief(patch)
    assert brief.startswith("apply_patch:")
    assert "base.css" in brief
    assert "StopIteration" not in brief


def test_format_patch_preview_block_multiline() -> None:
    block = format_patch_preview_block(UPDATE_PATCH)
    assert "Patch preview" in block
    assert "calc.py" in block


def test_turn_checkpoint_save_load(tmp_path: Path) -> None:
    store = TurnCheckpointStore(tmp_path / "cp")
    cp = TurnCheckpoint(
        thread_id="t1",
        turn_id="turn1",
        user_text="fix tests",
        messages=[{"role": "user", "content": "hi"}],
    )
    store.save(cp)
    loaded = store.load("t1", "turn1")
    assert loaded is not None
    assert loaded.user_text == "fix tests"
    assert loaded.messages[0]["role"] == "user"


def test_turn_checkpoint_find_latest(tmp_path: Path) -> None:
    store = TurnCheckpointStore(tmp_path / "cp")
    store.save(TurnCheckpoint("t1", "turn-a", "a", messages=[]))
    store.save(TurnCheckpoint("t1", "turn-b", "b", messages=[]))
    latest = store.find_latest("t1")
    assert latest is not None
    assert latest.turn_id == "turn-b"


def test_save_turn_checkpoint_respects_disabled() -> None:
    from agent.turn_checkpoint import TurnCheckpointSettings

    path = save_turn_checkpoint(
        settings=TurnCheckpointSettings(enabled=False),
        thread_id="t",
        turn_id="u",
        user_text="x",
        messages=[],
    )
    assert path is None


def test_summarize_thread_for_pr_bullets() -> None:
    thread = Thread(id=new_id(), cwd=".", model="m")
    turn = Turn(status="completed")
    turn.items.append(AgentMessageItem(text="Fixed calc.add bug."))
    turn.items.append(FileChangeItem(path="calc.py", status="completed", change_type="update"))
    turn.items.append(
        CommandExecutionItem(command="pytest -q", cwd=".", status="completed")
    )
    thread.turns.append(turn)
    bullets = summarize_thread_for_pr(thread)
    joined = " ".join(bullets)
    assert "calc.py" in joined
    assert "pytest" in joined.lower()
    assert "Fixed calc" in joined


def test_build_pr_description_summary_only() -> None:
    thread = Thread(id=new_id(), cwd=".", model="m", title="Fix")
    turn = Turn(status="completed")
    turn.items.append(UserMessageItem(text="fix"))
    turn.items.append(AgentMessageItem(text="Done fixing."))
    thread.turns.append(turn)
    md = build_pr_description(thread, include_diff=False, include_conversation=False)
    assert "## Summary" in md
    assert "## Conversation" not in md
    assert "Done fixing" in md


def test_resume_turn_from_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    cp_dir = tmp_path / "turn-cp"
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        f"""
[turn_checkpoint]
enabled = true
dir = "{cp_dir.as_posix()}"
""",
        encoding="utf-8",
    )
    thread = Thread(id="thread-resume", cwd=str(tmp_path), model="anthropic/claude-sonnet-4")
    turn = Turn(id="turn-resume", status="cancelled")
    turn.items.append(UserMessageItem(text="fix tests"))
    turn.items.append(AgentMessageItem(text="Turn cancelled by user."))
    thread.turns.append(turn)
    store = __import__("agent.store", fromlist=["ThreadStore"]).ThreadStore(
        base_dir=tmp_path / "threads"
    )
    store.create_thread(thread)
    messages = [{"role": "user", "content": "fix tests"}, {"role": "assistant", "content": "partial"}]
    TurnCheckpointStore(cp_dir).save(
        TurnCheckpoint(
            thread_id=thread.id,
            turn_id=turn.id,
            user_text="fix tests",
            messages=messages,
            status="cancelled",
        )
    )
    config = Config.resolve(cwd=tmp_path, auto_approve=True)
    resume_cp = TurnCheckpointStore(cp_dir).find_latest(thread.id)

    from model.openrouter import CompletionResult

    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.stream_completion.return_value = CompletionResult(
            "Resumed and done.",
            [],
            "stop",
            {"prompt_tokens": 10, "completion_tokens": 5},
        )
        completed = run_turn(
            thread,
            resume_cp.user_text,
            config,
            store,
            resume_checkpoint=resume_cp,
        )
    assert completed.status == "completed"
    assert completed.id == turn.id
    assert not TurnCheckpointStore(cp_dir).load(thread.id, turn.id)


def test_threads_checkpoint_status_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    cp_dir = tmp_path / "cp"
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        f"""
[turn_checkpoint]
enabled = true
dir = "{cp_dir.as_posix()}"
""",
        encoding="utf-8",
    )
    store = __import__("agent.store", fromlist=["ThreadStore"]).ThreadStore(
        base_dir=tmp_path / "threads"
    )
    thread = Thread(id="cpthread1", cwd=str(tmp_path), model="m")
    store.create_thread(thread)
    TurnCheckpointStore(cp_dir).save(
        TurnCheckpoint("cpthread1", "turn1", "hello", messages=[])
    )
    result = runner.invoke(app, ["threads", "checkpoint-status", "cpthread"])
    assert result.exit_code == 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "turn1" in out


def test_install_repl_completer_with_mock_readline(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".agent-cli" / "skills" / "pytest-fix"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: pytest-fix\ndescription: x\n---\n",
        encoding="utf-8",
    )
    fake_readline = MagicMock()
    with patch.dict("sys.modules", {"readline": fake_readline}):
        ok = install_repl_completer(tmp_path)
    assert ok is True
    fake_readline.set_completer.assert_called_once()
    fake_readline.parse_and_bind.assert_called_with("tab: complete")


def test_repl_commands_list_includes_skills() -> None:
    assert "/skills" in REPL_COMMANDS


def test_output_handler_patch_diff_preview() -> None:
    err = StringIO()
    handler = OutputHandler(stderr=Console(file=err, force_terminal=True, width=120))
    from agent.events import AgentEvent

    handler.handle(
        AgentEvent(
            type="tool.completed",
            data={
                "tool_name": "apply_patch",
                "status": "completed",
                "diff_preview": "+return a + b\n-return a - b",
            },
        )
    )
    text = err.getvalue()
    assert "apply_patch" in text
    assert "+return" in text


def test_doctor_openrouter_split_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with patch(
        "cli.main.check_openrouter_health",
        return_value=OpenRouterHealth("ok", "ok", "models API ok"),
    ):
        result = runner.invoke(app, ["doctor"])
    out = (result.stdout or "") + (result.stderr or "")
    assert "OpenRouter reachability" in out
    assert "OpenRouter API key" in out


def test_threads_pr_description_summary_only_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    store = __import__("agent.store", fromlist=["ThreadStore"]).ThreadStore(
        base_dir=tmp_path / "threads"
    )
    thread = Thread(id="sumthread1", cwd=str(tmp_path), model="m")
    turn = Turn(status="completed")
    turn.items.append(AgentMessageItem(text="Summary line."))
    thread.turns.append(turn)
    store.create_thread(thread)
    store.append_turn(thread, turn)
    result = runner.invoke(
        app,
        ["threads", "pr-description", "sumthread", "--summary-only", "--no-diff"],
    )
    assert result.exit_code == 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "Summary line" in out
    assert "## Conversation" not in out


def test_format_tool_summary_apply_patch_preview() -> None:
    from approval.gate import format_tool_summary

    summary = format_tool_summary("apply_patch", {"patch": UPDATE_PATCH})
    assert "calc.py" in summary
    assert "Patch preview" in summary
