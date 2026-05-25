from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.config_validate import validate_config
from agent.exec_policy import ExecPolicyMode
from agent.logging import log_info
from agent.metrics import MetricsCollector
from agent.settings import ServeSettings, SshExecutionSettings


def test_prometheus_text_format() -> None:
    MetricsCollector.reset_for_tests()
    m = MetricsCollector.global_collector()
    m.inc("turns_completed", 3)
    m.inc_labeled("agent_sync_bytes_total", "up", 1024)
    m.set_gauge("http_turns_active", 1)
    text = m.to_prometheus()
    assert text.endswith("\n")
    assert "# TYPE agent_turns_completed_total counter" in text
    assert 'direction="up"' in text
    assert "# TYPE http_turns_active gauge" in text or "# TYPE agent_http_turns_active gauge" in text


def test_config_validate_ssh_host_missing() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.execution.backend = "ssh"
    cfg.execution.ssh = SshExecutionSettings(host="", user="", remote_workspace="")
    result = validate_config(cfg)
    assert any(i.level == "error" and "host" in i.message for i in result.errors)
    assert result.exit_code() == 1


def test_config_validate_exec_policy_warning() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.exec_policy.mode = ExecPolicyMode.NEVER
    cfg.approval_mode = "interactive"
    result = validate_config(cfg)
    assert any("exec_policy=never" in i.message for i in result.warnings)


def test_config_validate_strict_warnings_exit_one() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.exec_policy.mode = ExecPolicyMode.NEVER
    cfg.approval_mode = "interactive"
    result = validate_config(cfg)
    assert result.exit_code(strict=True) == 1


def test_config_validate_ok_when_clean(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="sk-test")
    cfg.execution.backend = "local"
    result = validate_config(cfg)
    assert result.exit_code() == 0


def test_json_logging_format(capsys, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LOG_FORMAT", "json")
    log_info("turn.start", thread_id="t1", message="hello")
    err = capsys.readouterr().err
    payload = json.loads(err.strip())
    assert payload["event"] == "turn.start"
    assert payload["thread_id"] == "t1"
    assert payload["level"] == "info"


def test_text_logging_when_not_json(capsys, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LOG_FORMAT", raising=False)
    log_info("sync.done", message="ok")
    err = capsys.readouterr().err
    assert "sync.done" in err


def test_metrics_labeled_tool_calls() -> None:
    MetricsCollector.reset_for_tests()
    m = MetricsCollector.global_collector()
    m.inc_labeled("agent_tool_calls_total", "run_command", 5)
    snap = m.snapshot()
    assert snap.labeled_counters["agent_tool_calls_total"]["run_command"] == 5


def test_config_validate_serve_remote_bind_warning() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")

    with patch("agent.config_validate.load_serve_settings") as load:
        load.return_value = ServeSettings(allow_remote_bind=True, auth_token="")
        result = validate_config(cfg)
    assert any("allow_remote_bind" in i.message for i in result.warnings)


def test_config_validate_push_pull_remote_wins_warning() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.execution.backend = "ssh"
    cfg.execution.ssh.sync_enabled = True
    cfg.execution.ssh.sync_mode = "push-pull"
    cfg.execution.ssh.sync.conflict_strategy = "remote-wins"
    result = validate_config(cfg)
    assert any("push-pull" in i.message for i in result.warnings)
