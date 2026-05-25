from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from agent.config import Config
from agent.env_loader import load_env_file
from agent.events import AgentEvent
from agent.export.pr_description import build_pr_description
from agent.model_routing import load_model_routing, resolve_model_profile_from_task
from agent.models import AgentMessageItem, Thread, Turn, Usage, UserMessageItem, new_id
from agent.output_handler import OutputHandler, format_run_summary
from agent.providers.openrouter import probe_openrouter_reachability, recommend_model
from agent.store import ThreadStore
from cli.main import app

runner = CliRunner()


def test_probe_openrouter_missing_key() -> None:
    status, detail = probe_openrouter_reachability(None)
    assert status == "missing"
    assert "OPENROUTER" in detail


def test_probe_openrouter_reachable() -> None:
    resp = MagicMock(status_code=200)

    def fake_get(url: str, *, api_key: str):
        assert api_key == "good-key"
        return resp

    status, detail = probe_openrouter_reachability("good-key", http_get=fake_get)
    assert status == "reachable"
    assert "ok" in detail


def test_probe_openrouter_auth_failed() -> None:
    resp = MagicMock(status_code=401)
    status, _ = probe_openrouter_reachability("bad-key", http_get=lambda *a, **k: resp)
    assert status == "invalid"


def test_probe_openrouter_unreachable() -> None:
    def boom(*args, **kwargs):
        raise ConnectionError("network down")

    status, detail = probe_openrouter_reachability("k", http_get=boom)
    assert status == "unreachable"
    assert "network" in detail


def test_format_run_summary_includes_cost() -> None:
    turn = Turn(
        usage=Usage(
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.012,
            model_used="anthropic/claude-sonnet-4",
            fallback_used=True,
        )
    )
    line = format_run_summary(turn)
    assert "[done]" in line
    assert "fallback=true" in line
    assert "cost≈$0.012" in line


def test_output_handler_jsonl_mode() -> None:
    buf = StringIO()
    out = Console(file=buf, force_terminal=True, width=120)
    handler = OutputHandler(jsonl_events=True, stdout=out)
    event = AgentEvent(type="agent.delta", data={"text": "hi"})
    handler.handle(event)
    assert '"type": "agent.delta"' in buf.getvalue() or '"agent.delta"' in buf.getvalue()


def test_output_handler_tool_pending() -> None:
    err = StringIO()
    handler = OutputHandler(quiet_tools=False, stderr=Console(file=err, force_terminal=True, width=120))
    handler.handle(
        AgentEvent(
            type="tool.pending",
            data={"tool_name": "run_command", "arguments": {"cmd": "pytest -q"}},
        )
    )
    assert "run_command" in err.getvalue()


def test_output_handler_quiet_tools() -> None:
    err = StringIO()
    handler = OutputHandler(quiet_tools=True, stderr=Console(file=err, force_terminal=True, width=120))
    handler.handle(AgentEvent(type="tool.pending", data={"tool_name": "read_file", "arguments": {}}))
    assert err.getvalue() == ""


def test_output_handler_sandbox_blocked() -> None:
    err = StringIO()
    handler = OutputHandler(stderr=Console(file=err, force_terminal=True, width=120))
    handler.handle(
        AgentEvent(
            type="sandbox.blocked",
            data={"mode": "read-only", "reason": "write denied", "command": "echo x > f"},
        )
    )
    text = err.getvalue()
    assert "blocked" in text.lower()
    assert "read-only" in text


def test_env_loader_skips_comments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FOO_BAR_TEST", raising=False)
    env = tmp_path / ".env"
    env.write_text("# comment\nFOO_BAR_TEST=from-env\n", encoding="utf-8")
    assert load_env_file(env) == 1
    assert os.environ["FOO_BAR_TEST"] == "from-env"


def test_env_loader_export_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("EXPORT_ME", raising=False)
    env = tmp_path / ".env"
    env.write_text("export EXPORT_ME=1\n", encoding="utf-8")
    load_env_file(env)
    assert os.environ["EXPORT_ME"] == "1"


def test_env_loader_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OVERRIDE_ME", "old")
    env = tmp_path / ".env"
    env.write_text("OVERRIDE_ME=new\n", encoding="utf-8")
    load_env_file(env, override=True)
    assert os.environ["OVERRIDE_ME"] == "new"


def test_routing_disabled_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / ".agent-cli" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[model_routing]\nenabled = false\n", encoding="utf-8")
    routing = load_model_routing(project_path=cfg)
    assert routing.enabled is False
    assert resolve_model_profile_from_task("fix pytest tests", cwd=tmp_path) is None


def test_routing_no_match_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / ".agent-cli" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        """
[model_routing]
enabled = true
rules = [{ match = "deploy only", profile = "fast" }]
""",
        encoding="utf-8",
    )
    assert resolve_model_profile_from_task("hello world", cwd=tmp_path) is None


def test_routing_explain_to_fast() -> None:
    assert resolve_model_profile_from_task("explain this function") == "fast"


def test_recommend_model_uses_routed_profile(tmp_path: Path) -> None:
    cfg = tmp_path / ".agent-cli" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
""",
        encoding="utf-8",
    )
    models = [{"id": "google/gemini-flash", "name": "Flash"}]
    pick = recommend_model("fix failing tests", models, cwd=tmp_path)
    assert pick == "anthropic/claude-sonnet-4"


def test_build_pr_description_summary() -> None:
    thread = Thread(id=new_id(), cwd=".", model="m", title="Fix calc")
    turn = Turn(status="completed")
    turn.items.append(UserMessageItem(text="fix tests"))
    turn.items.append(AgentMessageItem(text="Patched calc.add to use +."))
    thread.turns.append(turn)
    md = build_pr_description(thread, include_diff=False)
    assert "## Fix calc" in md
    assert "Patched calc.add" in md
    assert "## Summary" in md


def test_build_pr_description_forked_from() -> None:
    thread = Thread(id=new_id(), cwd=".", model="m", forked_from="parent-id")
    thread.turns.append(Turn(status="completed"))
    md = build_pr_description(thread, include_diff=False)
    assert "parent-id" in md


def test_build_pr_description_git_diff(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t.com",
        },
    )
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    thread = Thread(id=new_id(), cwd=str(tmp_path), model="m")
    thread.turns.append(Turn(status="completed"))
    md = build_pr_description(thread)
    assert "Git diff stat" in md
    assert "a.txt" in md


def test_threads_pr_description_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    store = ThreadStore()
    thread = Thread(id="prthread1", cwd=str(tmp_path), model="m", title="My PR")
    turn = Turn(status="completed")
    turn.items.append(AgentMessageItem(text="Ship it."))
    thread.turns.append(turn)
    store.create_thread(thread)
    store.append_turn(thread, turn)
    result = runner.invoke(app, ["threads", "pr-description", "prthread"])
    assert result.exit_code == 0
    assert "Ship it." in (result.stdout or "")


def test_doctor_shows_openrouter_reachability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with patch(
        "cli.main.check_openrouter_health",
        return_value=__import__(
            "agent.providers.openrouter", fromlist=["OpenRouterHealth"]
        ).OpenRouterHealth("ok", "ok", "models API ok"),
    ):
        result = runner.invoke(app, ["doctor"])
    out = (result.stdout or "") + (result.stderr or "")
    assert result.exit_code == 0
    assert "OpenRouter reachability" in out


def test_load_env_file_missing_returns_zero(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "missing.env") == 0


def test_output_handler_agent_delta_stdout() -> None:
    out = StringIO()
    handler = OutputHandler(stdout=Console(file=out, force_terminal=True, width=120))
    handler.handle(AgentEvent(type="agent.delta", data={"text": "stream"}))
    assert "stream" in out.getvalue()


def test_build_pr_description_title_override() -> None:
    thread = Thread(id=new_id(), cwd=".", model="m")
    thread.turns.append(Turn(status="completed"))
    md = build_pr_description(thread, title="Custom title", include_diff=False)
    assert "## Custom title" in md
