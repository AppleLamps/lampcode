from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from agent.config import Config
from agent.init_scaffold import init_project
from agent.models import Thread, Turn, Usage, new_id
from agent.profiles import merge_layered_config, thread_cost_summary, load_model_profiles
from agent.providers.openrouter import (
    ModelsCache,
    classify_http_status,
    enrich_usage,
    estimate_cost_usd,
    model_chain,
    recommend_model,
    should_fallback,
)
from agent.repl import ReplSession
from agent.settings import OpenRouterSettings, SwarmBudgetPricing
from agent.skills.doctor import diagnose_skills, skills_doctor_report
from cli.main import app
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.git_commit import git_commit
from tools.registry import get_tool_schemas, tool_requires_approval, dispatch_tool


runner = CliRunner()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "file.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
        },
    )


def _config(**kwargs) -> Config:
    defaults = dict(cwd=Path("."), model="anthropic/claude-sonnet-4", openrouter_api_key="test-key")
    defaults.update(kwargs)
    return Config(**defaults)


def _cli_out(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


# --- OpenRouter provider ---


def test_classify_http_status() -> None:
    assert classify_http_status(429) == "rate_limit"
    assert classify_http_status(503) == "provider_error"
    assert classify_http_status(404) == "model_not_found"


def test_should_fallback_respects_chain_end() -> None:
    settings = OpenRouterSettings(fallback_on=["rate_limit"])
    assert not should_fallback(settings=settings, error_kind="rate_limit", model_index=1, total_models=2)
    assert should_fallback(settings=settings, error_kind="rate_limit", model_index=0, total_models=2)


def test_model_chain_dedupes() -> None:
    cfg = _config(
        model="a",
        openrouter=OpenRouterSettings(primary_model="a", fallback_models=["b", "a"]),
    )
    chain = model_chain(cfg)
    assert chain.models == ["a", "b"]


def test_estimate_cost_usd() -> None:
    pricing = {
        "anthropic/claude-sonnet-4": SwarmBudgetPricing(input_per_million=3.0, output_per_million=15.0),
    }
    cost = estimate_cost_usd("anthropic/claude-sonnet-4", 1_000_000, 100_000, pricing)
    assert cost == pytest.approx(4.5)


def test_enrich_usage() -> None:
    cfg = _config(
        openrouter=OpenRouterSettings(
            pricing={"anthropic/claude-sonnet-4": SwarmBudgetPricing(3.0, 15.0)}
        )
    )
    out = enrich_usage(
        cfg,
        model_used="anthropic/claude-sonnet-4",
        fallback_used=True,
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
    )
    assert out["model_used"] == "anthropic/claude-sonnet-4"
    assert out["fallback_used"] is True
    assert out["estimated_cost_usd"] > 0


def test_models_cache_roundtrip(tmp_path: Path) -> None:
    cache = ModelsCache(path=tmp_path / "models.json", ttl_sec=3600)
    models = [{"id": "test/model", "name": "Test"}]
    cache.save(models)
    assert cache.load()[0]["id"] == "test/model"


def test_recommend_model_fix_tests() -> None:
    models = [
        {"id": "google/gemini-flash", "name": "Gemini Flash"},
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet"},
    ]
    pick = recommend_model("fix tests", models)
    assert "claude" in pick or "sonnet" in pick


# --- Fallback chain (mocked) ---


def test_fallback_primary_503_uses_secondary() -> None:
    cfg = _config(
        model="primary/model",
        openrouter=OpenRouterSettings(
            fallback_models=["fallback/model"],
            fallback_on=["provider_error"],
            max_retries=0,
        ),
    )
    client = OpenRouterClient(cfg)

    fail_body = b'{"error":"unavailable"}'
    ok_chunks = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
        "data: [DONE]\n",
    ]

    call_count = {"n": 0}

    def fake_stream(*args, **kwargs):
        call_count["n"] += 1
        mock_resp = MagicMock()
        if call_count["n"] == 1:
            mock_resp.status_code = 503
            mock_resp.read.return_value = fail_body
            mock_resp.headers = {}
            mock_resp.iter_lines.return_value = iter([])
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=mock_resp)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = iter(ok_chunks)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_resp)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    with patch.object(client, "_sleep_backoff"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.stream.side_effect = fake_stream
            mock_client_cls.return_value = mock_client

            result = client.stream_completion([{"role": "user", "content": "hi"}])
            assert result.content == "ok"
            assert result.model_used == "fallback/model"
            assert result.fallback_used is True


def test_404_hint_models_list() -> None:
    client = OpenRouterClient(_config())
    err = client._make_api_error(404, "not found", "missing/model")
    assert "agent models list" in str(err)


# --- Profiles & init ---


def test_init_creates_files(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    created = init_project(tmp_path, yes=True)
    assert "config" in created
    assert (tmp_path / ".agent-cli" / "config.toml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()


def test_init_skips_without_yes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    init_project(tmp_path, yes=True)
    result = init_project(tmp_path, yes=False)
    assert "config" not in result
    assert "skipped_config" in result or "message" in result


def test_profile_merge_model_profile(tmp_path: Path) -> None:
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
model_profile = "fast"

[model_profiles.fast]
model = "google/gemini-flash"
max_tool_rounds = 12
""",
        encoding="utf-8",
    )
    merged = merge_layered_config(tmp_path, cli_model_profile="fast")
    assert merged["model"] == "google/gemini-flash"
    assert merged["max_tool_rounds"] == 12


def test_cli_overrides_profile(tmp_path: Path) -> None:
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text('profile = "ci"\nmodel = "from-project"\n', encoding="utf-8")
    merged = merge_layered_config(tmp_path, cli_profile="ci", cli_model="cli-model")
    assert merged["model"] == "cli-model"


def test_load_model_profiles(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
max_tool_rounds = 40
""",
        encoding="utf-8",
    )
    profiles = load_model_profiles(cfg)
    assert profiles["deep"].max_tool_rounds == 40


def test_config_resolve_model_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.fast]
model = "google/gemini-flash"
max_tool_rounds = 10
""",
        encoding="utf-8",
    )
    cfg = Config.resolve(cwd=tmp_path, model_profile="fast")
    assert cfg.model == "google/gemini-flash"
    assert cfg.max_rounds == 10


# --- Cost persistence ---


def test_thread_cost_summary() -> None:
    thread = Thread(id=new_id(), cwd=".", model="m")
    t1 = Turn(usage=Usage(input_tokens=100, output_tokens=50, estimated_cost_usd=0.01, model_used="a"))
    t2 = Turn(usage=Usage(input_tokens=200, output_tokens=100, estimated_cost_usd=0.02, model_used="b", fallback_used=True))
    thread.turns = [t1, t2]
    summary = thread_cost_summary(thread)
    assert summary["input_tokens"] == 300
    assert summary["estimated_cost_usd"] == pytest.approx(0.03)
    assert summary["models_used"] == ["a", "b"]


def test_threads_cost_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.store import ThreadStore

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    store = ThreadStore()
    thread = Thread(id="abc123", cwd=str(tmp_path), model="m")
    thread.turns.append(Turn(usage=Usage(input_tokens=10, output_tokens=5, estimated_cost_usd=0.001)))
    store.create_thread(thread)
    store.append_turn(thread, thread.turns[0])
    result = runner.invoke(app, ["threads", "cost", "abc"])
    assert result.exit_code == 0
    assert "estimated_cost_usd" in _cli_out(result)


# --- REPL ---


def test_repl_quit_command() -> None:
    cfg = _config()
    session = ReplSession(config=cfg, input_fn=lambda: "/quit", print_fn=lambda s: None)
    session.run()


def test_repl_thread_command() -> None:
    cfg = _config()
    lines: list[str] = []
    session = ReplSession(config=cfg, print_fn=lines.append)
    assert session.handle_line("/thread") is True
    assert any("thread" in x for x in lines)


def test_repl_model_command() -> None:
    cfg = _config()
    session = ReplSession(config=cfg, print_fn=lambda s: None)
    session.handle_line("/model fast-model")
    assert session.model_override == "fast-model"


def test_repl_skills_command(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    session = ReplSession(config=cfg, print_fn=lambda s: None)
    assert session.handle_line("/skills") is True


# --- git_commit ---


def test_git_commit_requires_approval() -> None:
    assert tool_requires_approval("git_commit") is True


def test_git_commit_clean_tree(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        msg = git_commit("test", cwd=str(tmp_path))
    assert "clean" in msg.lower()


def test_git_commit_dispatch_mocked(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    with patch("tools.registry.git_commit", return_value="Committed."):
        result = dispatch_tool("git_commit", {"message": "fix"}, cfg)
    assert "Committed" in result.text


def test_git_commit_schema_in_git_repo(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    cfg = _config(cwd=tmp_path)
    names = [s["function"]["name"] for s in get_tool_schemas(None, cfg)]
    assert "git_commit" in names


def test_git_commit_not_in_non_git(tmp_path: Path) -> None:
    cfg = _config(cwd=tmp_path)
    names = [s["function"]["name"] for s in get_tool_schemas(None, cfg)]
    assert "git_commit" not in names


# --- Skills doctor ---


def test_skills_doctor_ok(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill\n---\n\nBody",
        encoding="utf-8",
    )
    with patch("agent.skills.doctor.discover_skills") as disc:
        from agent.skills.discovery import Skill

        disc.return_value = [
            Skill(name="demo", description="Demo", body="", path=skill_dir / "SKILL.md", source="project")
        ]
        report = skills_doctor_report(tmp_path)
    assert report["ok"] is True


def test_skills_doctor_duplicate(tmp_path: Path) -> None:
    with patch("agent.skills.doctor.discover_skills") as disc:
        from agent.skills.discovery import Skill

        disc.return_value = [
            Skill(name="dup", description="a", body="", path=Path("/a/SKILL.md"), source="project"),
            Skill(name="dup", description="b", body="", path=Path("/b/SKILL.md"), source="user"),
        ]
        issues = diagnose_skills(tmp_path)
    assert any("Duplicate" in i.message for i in issues)


def test_skills_doctor_cli(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skills", "doctor", "--cwd", str(tmp_path)])
    assert result.exit_code == 0


# --- CLI commands ---


def test_models_list_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cache = ModelsCache(path=tmp_path / "m.json")
    cache.save([{"id": "x/y", "name": "XY"}])
    with patch("cli.main.ModelsCache", return_value=cache):
        result = runner.invoke(app, ["models", "list"])
    assert result.exit_code == 0
    assert "x/y" in _cli_out(result)


def test_models_recommend_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cache = ModelsCache(path=tmp_path / "m.json")
    cache.save([{"id": "anthropic/claude-sonnet-4", "name": "Claude"}])
    with patch("cli.main.ModelsCache", return_value=cache):
        result = runner.invoke(app, ["models", "recommend", "--task", "fix tests"])
    assert result.exit_code == 0
    assert "Recommended" in _cli_out(result)


def test_tools_list_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    result = runner.invoke(app, ["tools", "list", "--cwd", str(tmp_path), "--no-mcp"])
    assert result.exit_code == 0
    assert "run_command" in _cli_out(result)


def test_init_cli_requires_git(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--cwd", str(tmp_path), "--yes"])
    assert result.exit_code == 1
    assert "git" in _cli_out(result).lower()


def test_init_cli_skip_git_check(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--cwd", str(tmp_path), "--yes", "--skip-git-check"])
    assert result.exit_code == 0
    assert "Created" in _cli_out(result) or "exists" in _cli_out(result).lower()


def test_init_cli_in_git_repo(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = runner.invoke(app, ["init", "--cwd", str(tmp_path), "--yes"])
    assert result.exit_code == 0
    assert "Created" in _cli_out(result)


def test_profile_list_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    result = runner.invoke(app, ["profile", "list", "--cwd", str(tmp_path)])
    assert result.exit_code == 0


def test_openrouter_headers_from_config() -> None:
    cfg = _config(openrouter=OpenRouterSettings(app_name="MyApp", app_url="https://example.com"))
    client = OpenRouterClient(cfg)
    headers = client._headers()
    assert headers["X-Title"] == "MyApp"
    assert headers["HTTP-Referer"] == "https://example.com"


def test_usage_model_fields_serialize() -> None:
    u = Usage(input_tokens=1, output_tokens=2, estimated_cost_usd=0.5, model_used="m", fallback_used=True)
    data = u.model_dump()
    assert data["fallback_used"] is True


def test_ide_lsp_probe() -> None:
    from agent.ide_lsp import probe_python_lsp, fetch_completions_stub

    probe = probe_python_lsp()
    assert isinstance(probe.available, bool)
    assert isinstance(fetch_completions_stub(path="a.py", line=1, col=0), list)


def test_load_openrouter_settings_merge(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    project = tmp_path / "project.toml"
    user.write_text('[openrouter]\napp_name = "user"\n', encoding="utf-8")
    project.write_text('[openrouter]\napp_name = "project"\n', encoding="utf-8")
    from agent.settings import load_openrouter_settings

    s = load_openrouter_settings(user, project_path=project)
    assert s.app_name == "project"


def test_profile_show_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text('model = "from-project"\n', encoding="utf-8")
    result = runner.invoke(app, ["profile", "show", "interactive", "--cwd", str(tmp_path)])
    assert result.exit_code == 0


def test_threads_show_usage_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.models import UserMessageItem
    from agent.store import ThreadStore

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    store = ThreadStore()
    thread = Thread(id="usage1", cwd=str(tmp_path), model="m")
    turn = Turn(usage=Usage(input_tokens=10, output_tokens=5, estimated_cost_usd=0.01, model_used="m"))
    turn.items.append(UserMessageItem(text="hi"))
    thread.turns.append(turn)
    store.create_thread(thread)
    store.append_turn(thread, turn)
    result = runner.invoke(app, ["threads", "show", "usage", "--usage"])
    assert result.exit_code == 0
    assert "Usage:" in _cli_out(result)


def test_doctor_solo_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Solo dev readiness" in _cli_out(result)


def test_merge_precedence_project_over_user(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text("model = 'user-model'\n", encoding="utf-8")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text("model = 'project-model'\n", encoding="utf-8")
    merged = merge_layered_config(tmp_path, user_path=user)
    assert merged["model"] == "project-model"


def test_reasoning_effort_in_model_profile(tmp_path: Path) -> None:
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
reasoning_effort = "high"
""",
        encoding="utf-8",
    )
    merged = merge_layered_config(tmp_path, cli_model_profile="deep")
    assert merged.get("_reasoning_effort") == "high"


def test_config_resolve_reasoning_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    project = tmp_path / ".agent-cli" / "config.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        """
[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
reasoning_effort = "high"
""",
        encoding="utf-8",
    )
    cfg = Config.resolve(cwd=tmp_path, model_profile="deep")
    assert cfg.reasoning_effort == "high"


def test_ide_completions_stub() -> None:
    from agent.ide_lsp import fetch_completions_stub, probe_python_lsp

    probe = probe_python_lsp()
    items = fetch_completions_stub(path="main.py", line=1, col=0)
    assert isinstance(items, list)
    if probe.available:
        assert items
