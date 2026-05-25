from __future__ import annotations

import os
import shutil
import subprocess
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.events import EventEmitter
from agent.loop import run_turn
from agent.models import Thread, new_id
from agent.settings import load_openrouter_settings
from agent.store import ThreadStore
from model.openrouter import CompletionResult


REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PROJECT = REPO_ROOT / "examples" / "demo-project"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "marker.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
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


def _copy_demo_project(tmp_path: Path) -> Path:
    dest = tmp_path / "demo"
    shutil.copytree(DEMO_PROJECT, dest)
    _init_git_repo(dest)
    return dest


def test_demo_project_golden_path_fixes_calc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    demo = _copy_demo_project(tmp_path)
    thread = Thread(id=new_id(), cwd=str(demo), model="anthropic/claude-sonnet-4")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=demo,
        model="anthropic/claude-sonnet-4",
        approval_mode="auto",
        openrouter_api_key="test-key",
        openrouter=load_openrouter_settings(project_path=demo / ".agent-cli" / "config.toml"),
    )
    assert config.auto_approve

    patch_text = """*** Begin Patch
*** Update File: calc.py
@@
 def add(a: int, b: int) -> int:
-    return a - b  # bug: should be +
+    return a + b
*** End Patch
"""

    stream_results = [
        CompletionResult(
            "",
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "arguments": '{"cmd":"pytest -q"}',
                    },
                }
            ],
            "tool_calls",
            {"prompt_tokens": 500, "completion_tokens": 50},
        ),
        CompletionResult(
            "",
            [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "arguments": json.dumps({"patch": patch_text}),
                    },
                }
            ],
            "tool_calls",
            {"prompt_tokens": 600, "completion_tokens": 80},
        ),
        CompletionResult(
            "Fixed calc.add; tests should pass.",
            [],
            "stop",
            {"prompt_tokens": 200, "completion_tokens": 30},
        ),
    ]

    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        results_iter = iter(stream_results)

        def _next_completion(*args, **kwargs):
            try:
                return next(results_iter)
            except StopIteration:
                return CompletionResult("done", [], "stop", None)

        mock_client_cls.return_value.stream_completion.side_effect = _next_completion
        turn = run_turn(
            thread,
            "fix failing tests",
            config,
            store,
            events=EventEmitter(),
            session_auto_approve=True,
        )

    assert turn.status == "completed"
    calc = (demo / "calc.py").read_text(encoding="utf-8")
    assert "return a + b" in calc
    assert turn.usage.estimated_cost_usd > 0

    proc = subprocess.run(
        ["python", "-m", "pytest", "-q"],
        cwd=demo,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
