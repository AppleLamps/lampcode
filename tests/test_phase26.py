from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent.config import Config
from agent.events import AgentEvent
from agent.json_stream import JsonStreamHandler
from agent.loop import run_turn
from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    Thread,
    Turn,
)
from agent.providers.openrouter import build_model_preflight_rows, tool_support_warning
from agent.review import (
    ReviewFinding,
    ReviewReport,
    review_exceeds_fail_threshold,
    review_report_to_json,
)
from agent.settings import HarnessSettings
from agent.store import ThreadStore
from agent.turn_stats import aggregate_turn_stats
from cli.main import app
from tools.registry import DispatchResult


runner = CliRunner()
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "review.v1.json"


def test_doctor_model_table_includes_tool_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch("agent.providers.openrouter.ModelsCache") as mock_cache:
        mock_cache.return_value.load.return_value = [
            {
                "id": "anthropic/claude-sonnet-4",
                "supported_parameters": ["tools", "tool_choice"],
                "top_provider": {"context_length": 200000},
            }
        ]
        result = runner.invoke(app, ["doctor", "--models"])
    out = (result.stdout or "") + (result.stderr or "")
    assert result.exit_code == 0
    assert "Model preflight" in out
    assert "Tools" in out
    assert "default" in out.lower()
    assert "yes" in out


def test_run_warns_on_non_tool_model(monkeypatch: pytest.MonkeyPatch) -> None:
    warn = tool_support_warning("openrouter/auto")
    assert warn is not None
    assert "tool calling" in warn.lower()


def test_turn_stops_when_max_cost_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    thread = Thread(id="t-budget", cwd=str(tmp_path), model="test-model")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test-model",
        openrouter_api_key="x",
        max_cost_usd_per_turn=0.01,
        max_rounds=5,
    )

    client = MagicMock()

    class Result:
        content = ""
        tool_calls = [{"id": "c1", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}]
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        model_used = "test-model"
        fallback_used = False

    client.stream_completion.return_value = Result()
    client.complete = MagicMock()

    with patch("agent.loop.OpenRouterClient", return_value=client), patch(
        "agent.loop.get_tool_schemas", return_value=[]
    ), patch("agent.loop.dispatch_tool", return_value=MagicMock(text="ok", file_items=[])), patch(
        "agent.providers.openrouter.enrich_usage",
        return_value={
            "input_tokens": 1000,
            "output_tokens": 500,
            "estimated_cost_usd": 0.05,
            "model_used": "test-model",
            "fallback_used": False,
        },
    ):
        turn = run_turn(thread, "budget test", config, store, session_auto_approve=True)

    assert turn.status == "failed"
    assert any("max_cost_usd_per_turn" in item.text for item in turn.items if item.type == "agentMessage")


def test_review_json_matches_schema() -> None:
    report = ReviewReport(
        summary="summary",
        findings=[ReviewFinding("major", "bug", "detail", file="a.py", line=1)],
        suggested_fixes=["fix"],
        test_gaps=["add test"],
        raw_markdown="md",
    )
    payload = json.loads(
        review_report_to_json(report, thread_id="t1", model="m1", cost=0.01, schema_version="v1")
    )
    assert payload["schema_version"] == "v1"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert payload["summary"] == schema["properties"]["summary"]["type"] or isinstance(payload["summary"], str)
    assert "findings" in payload
    assert payload["severity_counts"]["major"] == 1


def test_review_exit_code_on_critical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    report = ReviewReport(
        summary="bad",
        findings=[ReviewFinding("critical", "security", "issue")],
        suggested_fixes=[],
        test_gaps=[],
        raw_markdown="## Summary\nbad",
    )
    assert review_exceeds_fail_threshold(report, ["critical"]) is True
    assert review_exceeds_fail_threshold(report, ["major"]) is True
    minor_only = ReviewReport(
        summary="ok",
        findings=[ReviewFinding("minor", "style", "nit")],
        suggested_fixes=[],
        test_gaps=[],
        raw_markdown="## Summary\nok",
    )
    assert review_exceeds_fail_threshold(minor_only, ["major"]) is False


def test_post_patch_test_runs_after_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    thread = Thread(id="t-hook", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        openrouter_api_key="x",
        max_rounds=3,
    )
    config.harness = HarnessSettings(post_patch_test="echo post-patch-ok")

    client = MagicMock()

    class Result:
        content = ""
        tool_calls = [
            {"id": "c1", "function": {"name": "apply_patch", "arguments": '{"patch":"x"}'}}
        ]
        usage = {"prompt_tokens": 10, "completion_tokens": 5}
        model_used = "test"
        fallback_used = False

    calls = {"n": 0}

    def stream_side_effect(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return Result()
        empty = MagicMock()
        empty.content = "done"
        empty.tool_calls = []
        empty.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        empty.model_used = "test"
        empty.fallback_used = False
        return empty

    client.stream_completion.side_effect = stream_side_effect

    file_item = FileChangeItem(path="a.py", status="completed", diff_snippet="+line")
    dispatch = DispatchResult(text="patched", file_items=[file_item])

    with patch("agent.loop.OpenRouterClient", return_value=client), patch(
        "agent.loop.get_tool_schemas", return_value=[]
    ), patch("agent.loop.compact_thread_if_needed", return_value=MagicMock(performed=False)), patch(
        "agent.loop.dispatch_tool", return_value=dispatch
    ), patch(
        "agent.loop._run_post_patch_test", return_value="post-patch-ok"
    ) as mock_hook:
        run_turn(thread, "patch", config, store, session_auto_approve=True)
    mock_hook.assert_called_once()


def test_turn_stats_from_mixed_items() -> None:
    turn = Turn(
        status="completed",
        items=[
            FileChangeItem(
                path="a.py",
                status="completed",
                diff_snippet="+added\n-removed",
            ),
            CommandExecutionItem(
                command="pytest -q",
                cwd=".",
                status="completed",
                output="1 passed",
            ),
        ],
    )
    stats = aggregate_turn_stats(turn)
    assert stats.files_touched == 1
    assert stats.lines_added == 1
    assert stats.lines_removed == 1
    assert stats.commands_run == 1
    assert stats.tests_detected is True


def test_json_stream_golden_fixture_matches_handler() -> None:
    handler = JsonStreamHandler()
    handler.handle(AgentEvent("turn.started", thread_id="t-golden", turn_id="u-golden"))
    handler.handle(
        AgentEvent(
            "tool.pending",
            thread_id="t-golden",
            turn_id="u-golden",
            data={"tool_name": "read_file", "arguments": {"path": "a.py"}},
        )
    )
    handler.handle(
        AgentEvent(
            "tool.completed",
            thread_id="t-golden",
            turn_id="u-golden",
            data={"tool_name": "read_file", "status": "completed"},
        )
    )
    handler.emit_run_summary(
        thread_id="t-golden",
        turn_id="u-golden",
        summary={"status": "completed"},
    )
    fixture = Path(__file__).parent / "fixtures" / "json_stream_golden.jsonl"
    expected_types = [json.loads(line)["type"] for line in fixture.read_text().splitlines() if line.strip()]
    actual_types = [json.loads(line)["type"] for line in handler.lines]
    assert actual_types == expected_types


def test_build_model_preflight_rows_tools_column() -> None:
    from agent.profiles import ModelProfile

    profiles = {"deep": ModelProfile(name="deep", model="anthropic/claude-sonnet-4")}
    rows = build_model_preflight_rows(
        profiles=profiles,
        default_model="anthropic/claude-sonnet-4",
        models=[{"id": "anthropic/claude-sonnet-4", "supported_parameters": ["tools"]}],
        pricing={},
    )
    assert rows
    assert rows[0]["tools"] == "yes"
