from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.config import Config
from cli.main import app
from agent.execution.shell_session import ShellSession, pty_support_status
from agent.hooks.runner import HooksRunner, load_hooks_config
from agent.memories import MemoryStore, inject_memories_prompt
from agent.models import UserInputItem, parse_item
from agent.output_schema import load_output_schema, parse_final_output
from agent.session import HarnessSession
from agent.user_input import resolve_user_input
from agent.sandbox.enforcer import check_run_command
from agent.sandbox.policy import SandboxMode
from tools.registry import get_tool_schemas

runner = CliRunner()


def test_user_input_item_parse() -> None:
    data = {
        "type": "userInput",
        "question": "Pick?",
        "answer": "A",
        "selected_option": "A",
        "options": ["A", "B"],
    }
    item = parse_item(data)
    assert isinstance(item, UserInputItem)
    assert item.answer == "A"


def test_resolve_user_input_ci_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_INPUT_ANSWERS", json.dumps({"Pick?": "B"}))
    ans, sel, err = resolve_user_input("Pick?", ["A", "B"], headless_json=True)
    assert err is None
    assert ans == "B"


def test_resolve_user_input_non_tty_fails() -> None:
    ans, _, err = resolve_user_input("Q?", None, headless_json=True)
    assert ans is None
    assert err


def test_request_permissions_escalation() -> None:
    session = HarnessSession()
    cfg = Config.resolve()
    cfg.sandbox_mode = SandboxMode.READ_ONLY
    before = check_run_command("curl https://example.com", Path.cwd(), cfg.sandbox_mode)
    assert before.blocked
    session.grant_permission("network", duration="turn")
    after = check_run_command(
        "curl https://example.com", Path.cwd(), cfg.sandbox_mode, session=session
    )
    assert after.allowed


def test_plan_mode_tool_filter() -> None:
    cfg = Config.resolve()
    allowed = cfg.plan_mode.allowed_tools
    schemas = get_tool_schemas(config=cfg, allowed_tools=allowed)
    names = {s["function"]["name"] for s in schemas}
    assert "read_file" in names
    assert "apply_patch" not in names


def test_hooks_runner(tmp_path: Path) -> None:
    script = tmp_path / "hook.py"
    script.write_text(
        "import os, sys\nprint(os.environ.get('AGENT_HOOK_EVENT',''))\n",
        encoding="utf-8",
    )
    hooks_file = tmp_path / ".agent-cli" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(
        json.dumps({"on_tool_pending": [{"command": f"{sys.executable} {script}", "timeout_sec": 5}]}),
        encoding="utf-8",
    )
    hooks = load_hooks_config(tmp_path)
    assert "on_tool_pending" in hooks
    runner_obj = HooksRunner(cwd=tmp_path, settings=__import__("agent.settings", fromlist=["HooksSettings"]).HooksSettings(), hooks=hooks)
    errors = runner_obj.run("on_tool_pending", {"tool": "x"}, tool_name="read_file")
    assert not errors


def test_memories_crud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.settings import MemoriesSettings

    path = tmp_path / "memories.json"
    store = MemoryStore(path=path, settings=MemoriesSettings(path=str(path)))
    mem = store.add("Always run pytest", tags=["testing"])
    assert store.delete(mem.id)
    assert store.add("fix auth bug", source_thread_id="t1")
    hits = store.search("pytest auth")
    assert hits


def test_memories_injection_disabled_by_default() -> None:
    assert inject_memories_prompt("pytest auth") == ""


def test_output_schema_validation() -> None:
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    data, errs = parse_final_output('{"name": "ok"}', schema)
    assert data == {"name": "ok"}
    assert not errs
    _, errs2 = parse_final_output('{"x": 1}', schema)
    assert errs2


def test_output_schema_file_load(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text('{"type": "object"}', encoding="utf-8")
    assert load_output_schema(path)["type"] == "object"


def test_shell_session_oneshot(tmp_path: Path) -> None:
    from agent.settings import ShellSettings

    sess = ShellSession("s1", tmp_path, ShellSettings(enabled=True))
    result = sess.run("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.output.lower() or result.output


def test_pty_status() -> None:
    status = pty_support_status()
    assert "available" in status


def test_agent_run_json_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")

    with patch("agent.loop.OpenRouterClient") as mock_client:
        from model.openrouter import CompletionResult

        mock_client.return_value.stream_completion.return_value = CompletionResult(
            "ok", [], "stop", {"prompt_tokens": 1, "completion_tokens": 1}
        )
        result = runner.invoke(
            app,
            ["run", "echo test", "--cwd", str(tmp_path), "--json", "--auto-approve", "--skip-git-check"],
        )
    assert result.exit_code == 0
    assert "turn.started" in result.output
    assert "run.summary" in result.output


def test_golden_review_fixture(tmp_path: Path) -> None:
    """Fixture repo diff with intentional bug metadata."""
    from agent.review import build_review_user_prompt, collect_review_context

    (tmp_path / "calc.py").write_text("def add(a,b):\n    return a-b\n", encoding="utf-8")
    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [
            (0, " calc.py | 1 +-", ""),
            (0, "@@ -1 +1 @@\n-def add(a,b):\n-    return a+b\n+def add(a,b):\n+    return a-b\n", ""),
            (0, " M calc.py", ""),
        ]
        ctx = collect_review_context(tmp_path, mode="uncommitted")
    prompt = build_review_user_prompt(ctx)
    assert "calc.py" in prompt
    assert "a-b" in prompt or "diff" in prompt.lower()


def test_hooks_list_cli(tmp_path: Path) -> None:
    hooks_file = tmp_path / ".agent-cli" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text('{"on_turn_completed": [{"command": "echo ok"}]}', encoding="utf-8")
    result = runner.invoke(app, ["hooks", "list", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "on_turn_completed" in result.output


def test_memories_cli_add_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".agent-cli" / "memories.json"
    from agent.settings import MemoriesSettings

    with patch("agent.memories._default_path", return_value=path):
        result = runner.invoke(app, ["memories", "add", "remember to run tests"])
    assert result.exit_code == 0
    with patch("agent.memories._default_path", return_value=path):
        listed = runner.invoke(app, ["memories", "list"])
    assert listed.exit_code == 0


def test_runs_export_jsonl_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.events import AgentEvent
    from agent.recording.store import RunStore

    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("agent.recording.store.default_runs_dir", lambda: runs_dir)
    store = RunStore(base_dir=runs_dir)
    store.append_event("t1", "u1", AgentEvent("turn.started", thread_id="t1", turn_id="u1"))
    store.append_event(
        "t1",
        "u1",
        AgentEvent("tool.pending", thread_id="t1", turn_id="u1", data={"tool_name": "read_file"}),
    )
    result = runner.invoke(app, ["runs", "export", "u1", "--thread-id", "t1", "--format", "jsonl-v2"])
    assert result.exit_code == 0
    assert "tool.pending" in result.output


def test_plan_mode_cli_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    with patch("agent.loop.OpenRouterClient") as mock_client:
        from model.openrouter import CompletionResult

        mock_client.return_value.stream_completion.return_value = CompletionResult(
            "plan done", [], "stop", {"prompt_tokens": 1, "completion_tokens": 1}
        )
        with patch("tools.registry.get_tool_schemas") as mock_schemas:
            mock_schemas.return_value = []
            result = runner.invoke(
                app,
                ["run", "design refactor", "--cwd", str(tmp_path), "--plan", "--auto-approve", "--skip-git-check"],
            )
    assert result.exit_code == 0


def test_output_schema_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        '{"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}}',
        encoding="utf-8",
    )
    with patch("agent.loop.OpenRouterClient") as mock_client:
        from model.openrouter import CompletionResult

        mock_client.return_value.stream_completion.return_value = CompletionResult(
            '{"value": "hello"}', [], "stop", {"prompt_tokens": 1, "completion_tokens": 1}
        )
        result = runner.invoke(
            app,
            [
                "run",
                "extract",
                "--cwd",
                str(tmp_path),
                "--auto-approve",
                "--skip-git-check",
                "--output-schema",
                str(schema_path),
            ],
        )
    assert result.exit_code == 0
    assert "hello" in result.output


def test_session_permission_denied_readonly_review() -> None:
    from agent.loop import _handle_request_permissions
    from agent.events import EventEmitter

    session = HarnessSession()
    cfg = Config.resolve()
    emitter = EventEmitter()
    out = _handle_request_permissions(
        __import__("agent.models", fromlist=["Thread"]).Thread(id="t", cwd=".", model="m"),
        __import__("agent.models", fromlist=["Turn"]).Turn(),
        emitter,
        {"scope": "network", "reason": "need curl"},
        cfg,
        session=session,
        turn_state=__import__("approval.gate", fromlist=["TurnApprovalState"]).TurnApprovalState(),
        read_only_review=True,
    )
    assert "false" in out.lower()


def test_json_stream_legacy_compat() -> None:
    from agent.json_stream import JsonStreamHandler
    from agent.events import AgentEvent

    handler = JsonStreamHandler(emit_legacy=True)
    line = handler.handle(AgentEvent("turn.started", thread_id="t", turn_id="u"))
    assert line and '"type": "turn.started"' in line


def test_review_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [(0, "s", ""), (0, "d", ""), (0, "", "")]
        with patch("agent.loop.OpenRouterClient") as mock_client:
            from model.openrouter import CompletionResult

            mock_client.return_value.stream_completion.return_value = CompletionResult(
                "## Summary\nok\n\n## Findings\n\n## Suggested fixes\n\n## Test gaps\n",
                [],
                "stop",
                {},
            )
            result = runner.invoke(
                app,
                ["review", "--uncommitted", "--cwd", str(tmp_path), "--json", "--auto-approve", "--skip-git-check"],
            )
    assert result.exit_code == 0
    assert "summary" in result.output.lower()


def test_load_shell_settings_defaults() -> None:
    from agent.settings import load_shell_settings

    s = load_shell_settings()
    assert s.enabled is False
    assert s.persistent is True


def test_plan_mode_settings_tools() -> None:
    from agent.settings import load_plan_mode_settings

    pm = load_plan_mode_settings()
    assert "read_file" in pm.allowed_tools


def test_user_input_auto_approve() -> None:
    ans, sel, err = resolve_user_input("Q?", ["A", "B"], auto_approve=True)
    assert err is None
    assert ans == "A"


def test_memory_store_search_ranking(tmp_path: Path) -> None:
    from agent.settings import MemoriesSettings

    path = tmp_path / "m.json"
    store = MemoryStore(path=path, settings=MemoriesSettings(path=str(path)))
    store.add("python pytest fixtures")
    store.add("unrelated note about coffee")
    hits = store.search("pytest python")
    assert hits and "pytest" in hits[0].text


def test_output_schema_inline_string() -> None:
    schema = load_output_schema('{"type": "object", "properties": {"n": {"type": "number"}}}')
    data, errs = parse_final_output('{"n": 1}', schema)
    assert data == {"n": 1}
    assert not errs


def test_normalize_sandbox_blocked_to_error() -> None:
    from agent.json_stream import normalize_event
    from agent.events import AgentEvent

    norm = normalize_event(
        AgentEvent("sandbox.blocked", thread_id="t", turn_id="u", data={"reason": "x"})
    )
    assert norm.type == "error"


def test_hooks_fail_on_error_raises(tmp_path: Path) -> None:
    from agent.settings import HooksSettings

    hooks = {"on_tool_pending": [{"command": "exit 1", "timeout_sec": 5}]}
    runner_obj = HooksRunner(
        cwd=tmp_path,
        settings=HooksSettings(fail_on_error=True),
        hooks=hooks,
    )
    with pytest.raises(RuntimeError):
        runner_obj.run("on_tool_pending", {})


def test_shell_session_manager_new_session(tmp_path: Path) -> None:
    from agent.execution.shell_session import ShellSessionManager
    from agent.settings import ShellSettings

    mgr = ShellSessionManager()
    s1 = mgr.get("thread1", tmp_path, ShellSettings(enabled=True))
    s2 = mgr.get("thread1", tmp_path, ShellSettings(enabled=True), new_session=True)
    assert s1.session_id == s2.session_id


def test_review_report_to_json() -> None:
    from agent.review import ReviewFinding, ReviewReport, review_report_to_json

    report = ReviewReport(
        summary="s",
        findings=[ReviewFinding("major", "bug", "detail")],
        suggested_fixes=["fix"],
        test_gaps=["test"],
        raw_markdown="md",
    )
    payload = json.loads(review_report_to_json(report, thread_id="t", model="m", cost=0.01))
    assert payload["schema_version"] == "v1"
    assert payload["severity_counts"]["major"] == 1


def test_get_tool_schemas_filters_mcp_when_allowed() -> None:
    cfg = Config.resolve()
    schemas = get_tool_schemas(config=cfg, allowed_tools=["read_file"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file"}


def test_tool_requires_approval_new_tools() -> None:
    from tools.registry import tool_requires_approval

    assert tool_requires_approval("request_user_input")
    assert tool_requires_approval("request_permissions")


def test_harness_session_escalation_flags() -> None:
    from agent.session import HarnessSession

    s = HarnessSession()
    s.grant_permission("network", duration="turn")
    assert s.has_network_escalation()
    s.clear_turn_escalations()
    assert not s.turn_allow_network


def test_review_diff_truncation(tmp_path: Path) -> None:
    from agent.review import collect_review_context

    big = "x" * 300_000
    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [(0, "stat", ""), (0, big, ""), (0, "", "")]
        ctx = collect_review_context(tmp_path, mode="commit", commit="abc")
    assert "truncated" in ctx.diff_patch


def test_v2_event_types_include_permission() -> None:
    from agent.json_stream import V2_EVENT_TYPES

    assert "permission.escalated" in V2_EVENT_TYPES
    assert "hook.failed" in V2_EVENT_TYPES


def test_memories_delete_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".agent-cli" / "memories.json"
    from agent.memories import MemoryStore
    from agent.settings import MemoriesSettings

    store = MemoryStore(path=path, settings=MemoriesSettings(path=str(path)))
    mem = store.add("note")
    with patch("agent.memories._default_path", return_value=path):
        result = runner.invoke(app, ["memories", "delete", mem.id])
    assert result.exit_code == 0


def test_memories_search_cli(tmp_path: Path) -> None:
    path = tmp_path / ".agent-cli" / "memories.json"
    from agent.memories import MemoryStore
    from agent.settings import MemoriesSettings

    store = MemoryStore(path=path, settings=MemoriesSettings(path=str(path)))
    store.add("always run pytest before commit")
    with patch("agent.memories._default_path", return_value=path):
        result = runner.invoke(app, ["memories", "search", "pytest"])
    assert result.exit_code == 0
    assert "pytest" in result.output


def test_config_resolves_phase22_sections() -> None:
    cfg = Config.resolve()
    assert cfg.shell.enabled is False
    assert cfg.memories.enabled is False
    assert "read_file" in cfg.plan_mode.allowed_tools


def test_output_handler_json_stream_mode() -> None:
    from agent.events import AgentEvent
    from agent.output_handler import OutputHandler
    from io import StringIO
    from rich.console import Console

    buf = StringIO()
    out = OutputHandler(json_stream=True, stdout=Console(file=buf, width=200))
    out.handle(AgentEvent("turn.started", thread_id="t", turn_id="u"))
    assert "turn.started" in buf.getvalue()


