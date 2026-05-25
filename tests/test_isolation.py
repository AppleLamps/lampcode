import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config import Config
from agent.isolation.env import sanitize_environment
from agent.isolation.paths import enforce_workdir
from agent.isolation.runner import IsolationResult, kill_process_tree, run_isolated_command
from agent.sandbox.policy import SandboxMode
from agent.settings import IsolationSettings


def test_sanitize_environment_strips_unknown() -> None:
    source = {"PATH": "/bin", "SECRET_TOKEN": "x", "HOME": "/home/u"}
    env, stripped = sanitize_environment(
        source,
        allowed_vars=["PATH", "HOME"],
        strip_env=True,
        clear_network_env_hints=False,
    )
    assert env == {"PATH": "/bin", "HOME": "/home/u"}
    assert stripped == 1


def test_sanitize_clears_proxy_vars() -> None:
    source = {"PATH": "/bin", "HTTP_PROXY": "http://proxy", "HTTPS_PROXY": "x"}
    env, stripped = sanitize_environment(
        source,
        allowed_vars=["PATH"],
        strip_env=True,
        clear_network_env_hints=True,
    )
    assert "HTTP_PROXY" not in env
    assert stripped >= 1


def test_enforce_workdir_inside(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    resolved = enforce_workdir(tmp_path, "sub")
    assert resolved == sub.resolve()


def test_enforce_workdir_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        enforce_workdir(tmp_path, "../outside")


def test_run_isolated_command_mocked(tmp_path: Path) -> None:
    settings = IsolationSettings(strip_env=True)
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.communicate.return_value = ("ok", "")
    mock_proc.returncode = 0

    with patch("agent.isolation.runner.subprocess.Popen", return_value=mock_proc):
        result = run_isolated_command(
            tmp_path,
            "echo hi",
            timeout=5,
            settings=settings,
            source_env={"PATH": "/bin", "SECRET": "nope"},
        )
    assert isinstance(result, IsolationResult)
    assert result.output == "ok"
    assert result.pid == 1234
    assert result.stripped_env_count >= 1


def test_kill_process_tree_windows() -> None:
    with patch("agent.isolation.runner.sys.platform", "win32"):
        with patch("agent.isolation.runner.subprocess.run") as mock_run:
            kill_process_tree(999)
            mock_run.assert_called_once()
            assert "taskkill" in mock_run.call_args[0][0]


def test_config_use_isolation() -> None:
    cfg = Config(
        cwd=Path("."),
        model="test",
        openrouter_api_key="x",
        sandbox_mode=SandboxMode.WORKSPACE_WRITE,
        isolation=IsolationSettings(enabled=True),
    )
    assert cfg.use_isolation is True

    cfg2 = Config(
        cwd=Path("."),
        model="test",
        openrouter_api_key="x",
        sandbox_mode=SandboxMode.DANGER_FULL_ACCESS,
        isolation=IsolationSettings(enabled=True),
    )
    assert cfg2.use_isolation is False


def test_timeout_kills_tree(tmp_path: Path) -> None:
    settings = IsolationSettings(kill_process_tree_on_timeout=True)
    mock_proc = MagicMock()
    mock_proc.pid = 555
    mock_proc.communicate.side_effect = __import__("subprocess").TimeoutExpired("cmd", 1)

    kill_mock = MagicMock()
    with patch("agent.isolation.runner.subprocess.Popen", return_value=mock_proc):
        with patch("agent.isolation.runner.kill_process_tree", kill_mock):
            result = run_isolated_command(
                tmp_path,
                "sleep 99",
                timeout=1,
                settings=settings,
            )
    assert "timed out" in result.output
    kill_mock.assert_called_once_with(555)
