from pathlib import Path

import pytest

from agent.cancel import CancelToken, CancelledError
from agent.config import Config, FileConfig


def test_cancel_token() -> None:
    token = CancelToken()
    assert not token.cancelled
    token.cancel()
    assert token.cancelled
    with pytest.raises(CancelledError):
        token.check()


def test_config_precedence_cli_over_file(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".agent-cli"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text('model = "from-file"\n')
    monkeypatch.setattr("agent.config.default_config_path", lambda: config_file)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    cfg = Config.resolve(cwd=tmp_path, model="from-cli")
    assert cfg.model == "from-cli"


def test_execution_backend_precedence(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".agent-cli"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text(
        '[execution]\nbackend = "local"\ndefault_image = "python:3.12-slim"\n'
    )
    monkeypatch.setattr("agent.config.default_config_path", lambda: config_file)
    monkeypatch.delenv("AGENT_EXECUTION_BACKEND", raising=False)

    cfg = Config.resolve(cwd=tmp_path, execution_backend="docker")
    assert cfg.execution.backend == "docker"

    monkeypatch.setenv("AGENT_EXECUTION_BACKEND", "local")
    cfg2 = Config.resolve(cwd=tmp_path, execution_backend="docker")
    assert cfg2.execution.backend == "docker"

    cfg3 = Config.resolve(cwd=tmp_path)
    assert cfg3.execution.backend == "local"


def test_file_config_defaults() -> None:
    cfg = FileConfig.load(Path("/nonexistent/config.toml"))
    assert cfg.approval_mode == "interactive"
    assert cfg.max_tool_rounds == 25
