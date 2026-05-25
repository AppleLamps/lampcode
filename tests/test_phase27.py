from __future__ import annotations

import json
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent.config import Config
from agent.exec_policy import (
    append_allow_prefix,
    evaluate_command,
    load_allow_prefixes,
    matches_allow_prefix,
)
from agent.execution.conpty import ConPtyRunResult, conpty_available
from agent.execution.shell_session import ShellSession, pty_support_status
from agent.mcp_server.runner import run_agent_tool
from agent.mcp_server.schema import AGENT_RUN_INPUT_SCHEMA, AGENT_RUN_TOOL_NAME
from agent.mcp_server.server import create_server
from agent.models import AgentMessageItem, Thread, new_id
from agent.review import (
    ReviewContext,
    build_review_user_prompt,
    collect_review_context,
    review_report_to_json,
)
from agent.settings import ShellSettings, WebSearchSettings
from agent.store import ThreadStore
from cli.main import app
from tools.web_search import web_search

runner = CliRunner()


def test_conpty_probe_reports_unavailable_off_windows() -> None:
    if platform.system() == "Windows":
        pytest.skip("Windows may have pywinpty installed")
    assert conpty_available() is False


def test_shell_session_backend_auto_selects_pipes_on_windows_without_conpty(
    tmp_path: Path,
) -> None:
    settings = ShellSettings(enabled=True, persistent=True, backend="auto")
    with patch("agent.execution.shell_session.conpty_available", return_value=False):
        sess = ShellSession("s-auto", tmp_path, settings)
    assert sess._backend == "pipes"


def test_shell_session_write_stdin_round_trip(tmp_path: Path) -> None:
    settings = ShellSettings(enabled=True, persistent=True, backend="pipes")
    sess = ShellSession("s-stdin", tmp_path, settings)
    sess._ensure_process()
    assert sess._proc is not None
    sess.write_stdin("echo hello")
    sess.close()


def test_pty_support_status_includes_conpty_flag() -> None:
    status = pty_support_status()
    assert "conpty_available" in status
    assert status["backend"] in ("pipes", "pty", "conpty")


def test_shell_session_conpty_branch(tmp_path: Path) -> None:
    settings = ShellSettings(enabled=True, persistent=True, backend="conpty")
    mock_result = ConPtyRunResult(
        output="ok",
        exit_code=0,
        duration_ms=1,
        meta={"persistent": True, "backend": "conpty", "pty": True},
    )
    with patch("agent.execution.shell_session.conpty_available", return_value=True), patch(
        "agent.execution.shell_session.ConPtySession"
    ) as mock_cls:
        mock_cls.return_value.run.return_value = mock_result
        sess = ShellSession("s-conpty", tmp_path, settings)
        result = sess.run("echo ok")
    assert result.meta["backend"] == "conpty"
    assert result.output == "ok"


def test_mcp_server_tool_schema_lists_agent_run() -> None:
    server = create_server()
    assert server.name == "agent-cli"
    assert AGENT_RUN_TOOL_NAME == "agent_run"
    assert "prompt" in AGENT_RUN_INPUT_SCHEMA["required"]


def test_mcp_server_run_turn_mocked_openrouter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    thread = Thread(id=new_id(), cwd=str(tmp_path), model="test")
    fake_turn = _fake_turn(thread)

    with patch("agent.mcp_server.runner.run_turn", return_value=fake_turn):
        result = run_agent_tool({"prompt": "say hi", "cwd": str(tmp_path), "auto_approve": True})

    assert result["status"] == "completed"
    assert "Hello from MCP" in result["final_text"]
    assert result["thread_id"].startswith("ephemeral-")


def _fake_turn(thread: Thread):
    from agent.models import Turn, Usage

    turn = Turn()
    turn.status = "completed"
    turn.items.append(AgentMessageItem(type="agentMessage", text="Hello from MCP"))
    turn.usage = Usage(estimated_cost_usd=0.01, model_used="test-model")
    return turn


def test_mcp_server_respects_max_cost_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    thread = Thread(id=new_id(), cwd=str(tmp_path), model="test")
    cfg = Config(cwd=tmp_path, model="test", openrouter_api_key="x")

    with patch("agent.mcp_server.runner.Config.resolve", return_value=cfg) as mock_resolve, patch(
        "agent.mcp_server.runner.run_turn",
        return_value=_fake_turn(thread),
    ):
        run_agent_tool(
            {
                "prompt": "budget",
                "cwd": str(tmp_path),
                "max_cost": 0.25,
                "auto_approve": True,
            }
        )
    assert mock_resolve.call_args.kwargs["max_cost_usd"] == 0.25


def test_web_search_unknown_provider_returns_error() -> None:
    settings = WebSearchSettings(provider="unknown")
    results, err = web_search("query", settings)
    assert results == []
    assert err is not None
    assert "Unknown web search provider" in err


def test_web_search_exa_mocked_response() -> None:
    settings = WebSearchSettings(provider="exa", api_key="test-key", max_results=2)
    payload = {
        "results": [
            {"title": "Exa One", "url": "https://exa.one", "text": "snippet one"},
            {"title": "Exa Two", "url": "https://exa.two", "text": "snippet two"},
        ]
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    with patch("tools.web_search.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = FakeResponse()
        results, err = web_search("exa query", settings)

    assert err is None
    assert len(results) == 2
    assert results[0].title == "Exa One"


def test_web_search_tavily_mocked_response() -> None:
    settings = WebSearchSettings(provider="tavily", api_key="test-key", max_results=1)
    payload = {
        "results": [
            {"title": "Tavily One", "url": "https://tavily.one", "content": "content one"},
        ]
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    with patch("tools.web_search.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = FakeResponse()
        results, err = web_search("tavily query", settings)

    assert err is None
    assert len(results) == 1
    assert results[0].url == "https://tavily.one"


def test_doctor_web_search_row_shows_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch("cli.main.Config.resolve") as mock_resolve:
        mock_resolve.return_value = Config(
            cwd=Path("."),
            model="test",
            openrouter_api_key="x",
            web_search=WebSearchSettings(enabled=True, provider="exa", api_key_env="EXA_API_KEY"),
        )
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    output = (result.stdout or "") + (result.stderr or "")
    assert "provider=exa" in output


def test_exec_policy_allow_prefix_auto_approves_matching_command() -> None:
    policy = Config(
        cwd=Path("."),
        model="test",
        openrouter_api_key="x",
    ).exec_policy
    policy.allow_prefixes = ["pytest -q"]
    result = evaluate_command("pytest -q tests/test_x.py", policy)
    assert result["decision"] == "allow"
    assert result["auto_approve"] is True
    assert matches_allow_prefix("pytest -q tests", ["pytest -q"])


def test_exec_policy_amend_cli_appends_prefix(tmp_path: Path) -> None:
    result = runner.invoke(app, ["exec-policy", "amend", "--prefix", "pytest -q", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert load_allow_prefixes(tmp_path) == ["pytest -q"]


def test_exec_policy_amend_dedupes_prefix(tmp_path: Path) -> None:
    append_allow_prefix(tmp_path, "git status")
    append_allow_prefix(tmp_path, "git status")
    assert load_allow_prefixes(tmp_path) == ["git status"]


def test_review_custom_prompt_in_system_context() -> None:
    ctx = ReviewContext(
        mode="uncommitted",
        label="uncommitted",
        diff_stat="",
        diff_patch="",
        status="",
        title="review",
        custom_prompt="Focus on security",
    )
    prompt = build_review_user_prompt(ctx)
    assert "Focus on security" in prompt
    assert "Additional review focus" in prompt


def test_review_base_uses_merge_base_for_diff(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_git(cwd: Path, *args: str) -> tuple[int, str, str]:
        calls.append(list(args))
        if args and args[0] == "merge-base":
            return 0, "abc123", ""
        if args[0] == "diff" and args[1] == "abc123":
            return 0, "patch-body", ""
        if args[0] == "diff" and args[-1] == "--stat":
            return 0, " 1 file changed", ""
        if args[0] == "status":
            return 0, "", ""
        return 0, "", ""

    with patch("agent.review._run_git", side_effect=fake_git):
        ctx = collect_review_context(tmp_path, mode="base", base="main")

    assert ctx.merge_base_sha == "abc123"
    assert any(call[:2] == ["diff", "abc123"] for call in calls)


def test_review_json_includes_merge_base_sha() -> None:
    from agent.review import ReviewFinding, ReviewReport

    report = ReviewReport(
        summary="s",
        findings=[ReviewFinding("minor", "t", "d")],
        suggested_fixes=[],
        test_gaps=[],
        raw_markdown="md",
    )
    payload = json.loads(
        review_report_to_json(
            report,
            thread_id="t1",
            model="m1",
            cost=0.0,
            merge_base_sha="abc123",
            review_scope="base:main",
            custom_prompt="security",
        )
    )
    assert payload["merge_base_sha"] == "abc123"
    assert payload["review_scope"] == "base:main"
    assert payload["custom_prompt"] == "security"
