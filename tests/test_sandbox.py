from pathlib import Path

import pytest

from agent.sandbox.classifier import (
    CommandRisk,
    classify_command,
    extract_write_targets,
    mcp_tool_is_mutating,
)
from agent.sandbox.enforcer import (
    check_apply_patch,
    check_mcp_tool,
    check_run_command,
    check_write_file,
)
from agent.sandbox.policy import SandboxMode


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("cat file.txt", CommandRisk.READ),
        ("Get-Content x.py", CommandRisk.READ),
        ("dir", CommandRisk.READ),
        ("git status --short", CommandRisk.READ),
        ("pytest -q", CommandRisk.TEST),
        ("echo x > foo.txt", CommandRisk.WRITE),
        ("Set-Content out.txt hi", CommandRisk.WRITE),
        ("curl https://example.com", CommandRisk.NETWORK),
        ("pip install requests", CommandRisk.NETWORK),
        ("python script.py", CommandRisk.RUN),
    ],
)
def test_classify_command(cmd: str, expected: CommandRisk) -> None:
    assert classify_command(cmd) == expected


def test_extract_write_targets_redirect() -> None:
    assert extract_write_targets("echo hello > out.txt") == ["out.txt"]


def test_extract_write_targets_powershell() -> None:
    targets = extract_write_targets('Set-Content -Path "nested/out.txt" hi')
    assert "nested/out.txt" in targets


def test_mcp_tool_is_mutating() -> None:
    assert mcp_tool_is_mutating("mcp__fs__write_file")
    assert not mcp_tool_is_mutating("mcp__fs__read_file")


def test_read_only_blocks_write_command(tmp_path: Path) -> None:
    decision = check_run_command("echo x > foo.txt", tmp_path, SandboxMode.READ_ONLY)
    assert decision.blocked
    assert "write" in decision.reason


def test_read_only_blocks_network(tmp_path: Path) -> None:
    decision = check_run_command("curl https://x.com", tmp_path, SandboxMode.READ_ONLY)
    assert decision.blocked
    assert "network" in decision.reason


def test_read_only_allows_git_status(tmp_path: Path) -> None:
    decision = check_run_command("git status", tmp_path, SandboxMode.READ_ONLY)
    assert decision.allowed


def test_danger_allows_everything(tmp_path: Path) -> None:
    decision = check_run_command("del /s /q C:\\Windows", tmp_path, SandboxMode.DANGER_FULL_ACCESS)
    assert decision.allowed


def test_workspace_write_allows_in_cwd(tmp_path: Path) -> None:
    decision = check_run_command("echo x > local.txt", tmp_path, SandboxMode.WORKSPACE_WRITE)
    assert decision.allowed


def test_workspace_write_blocks_outside_cwd(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    decision = check_run_command(f"echo x > {outside}", tmp_path, SandboxMode.WORKSPACE_WRITE)
    assert decision.blocked
    assert "outside workspace" in decision.reason


def test_write_file_read_only(tmp_path: Path) -> None:
    decision = check_write_file("x.py", tmp_path, SandboxMode.READ_ONLY)
    assert decision.blocked


def test_write_file_workspace_inside(tmp_path: Path) -> None:
    decision = check_write_file("src/x.py", tmp_path, SandboxMode.WORKSPACE_WRITE)
    assert decision.allowed


def test_apply_patch_read_only(tmp_path: Path) -> None:
    decision = check_apply_patch(tmp_path, SandboxMode.READ_ONLY)
    assert decision.blocked


def test_mcp_mutating_read_only(tmp_path: Path) -> None:
    decision = check_mcp_tool("mcp__fs__write_file", SandboxMode.READ_ONLY)
    assert decision.blocked
