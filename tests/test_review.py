from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agent.review import collect_review_context, parse_review_markdown
from agent.sandbox.policy import SandboxMode
from cli.main import app

runner = CliRunner()


def test_collect_review_uncommitted(tmp_path: Path) -> None:
    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [
            (0, " 1 file changed", ""),
            (0, "diff content", ""),
            (0, " M foo.py", ""),
        ]
        ctx = collect_review_context(tmp_path, mode="uncommitted")
    assert ctx.mode == "uncommitted"
    assert ctx.title == "review: uncommitted"
    assert "diff content" in ctx.diff_patch


def test_collect_review_base(tmp_path: Path) -> None:
    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [
            (0, "abc123", ""),
            (0, "stat", ""),
            (0, "patch", ""),
            (0, "", ""),
        ]
        ctx = collect_review_context(tmp_path, mode="base", base="main")
    assert ctx.label == "changes vs main"
    assert ctx.title == "review: vs main"
    assert ctx.merge_base_sha == "abc123"


def test_collect_review_commit(tmp_path: Path) -> None:
    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [
            (0, "stat", ""),
            (0, "patch", ""),
        ]
        ctx = collect_review_context(tmp_path, mode="commit", commit="abc12345")
    assert "commit abc12345" in ctx.label


def test_parse_review_markdown() -> None:
    md = """## Summary
Looks good.

## Findings
- [major] Off-by-one — loop bound wrong (foo.py:10)
- [nit] typo

## Suggested fixes
- Fix loop

## Test gaps
- Add unit test
"""
    report = parse_review_markdown(md)
    assert report.summary.startswith("Looks good")
    assert len(report.findings) == 2
    assert report.findings[0].severity == "major"
    assert report.severity_counts["major"] == 1


def test_review_cli_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")

    with patch("agent.review._run_git") as mock_git:
        mock_git.side_effect = [
            (0, "stat", ""),
            (0, "+bug", ""),
            (0, " M x.py", ""),
        ]
        with patch("agent.loop.OpenRouterClient") as mock_client:
            from model.openrouter import CompletionResult

            mock_client.return_value.stream_completion.return_value = CompletionResult(
                """## Summary
Bug found.

## Findings
- [critical] Wrong return — subtracts instead of adds

## Suggested fixes
- Fix add()

## Test gaps
- pytest for add()
""",
                [],
                "stop",
                {"prompt_tokens": 10, "completion_tokens": 20},
            )
            result = runner.invoke(
                app,
                ["review", "--uncommitted", "--cwd", str(tmp_path), "--auto-approve", "--skip-git-check"],
            )
    assert result.exit_code == 0, result.output
    assert "Summary" in result.output or "Bug found" in result.output


def test_review_read_only_blocks_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")

    from agent.config import Config
    from agent.loop import _precheck_tool
    from agent.mcp.manager import McpManager

    cfg = Config.resolve(cwd=tmp_path, skip_git_check=True)
    cfg.sandbox_mode = SandboxMode.READ_ONLY
    mcp = McpManager({})
    reason, _retryable = _precheck_tool("apply_patch", {"patch": "x"}, cfg, mcp, read_only_review=True)
    assert reason and "read-only review" in reason
