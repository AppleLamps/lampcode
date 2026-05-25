from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agent.apply_last import apply_last_from_thread
from agent.context import build_system_prompt
from agent.memories import MemoryStore, inject_memories_prompt
from agent.models import FileChangeItem, Thread, Turn, new_id
from agent.notify import fire_notify
from agent.settings import MemoriesSettings, NotifySettings
from agent.store import ThreadStore, default_store_dir
from agent.threads_picker import list_threads_for_picker, pick_thread
from cli.main import app


runner = CliRunner()


def test_thread_picker_lists_by_cwd(tmp_path: Path) -> None:
    store_dir = tmp_path / "threads"
    store = ThreadStore(base_dir=store_dir)
    other = Thread(id=new_id(), cwd=str(tmp_path / "other"), model="test")
    local = Thread(id=new_id(), cwd=str(tmp_path), model="test")
    store.create_thread(other)
    store.create_thread(local)

    listed = list_threads_for_picker(store, tmp_path)
    assert len(listed) == 1
    assert listed[0].id == local.id


def test_resume_last_unchanged(tmp_path: Path) -> None:
    store_dir = tmp_path / "threads"
    store = ThreadStore(base_dir=store_dir)
    older = Thread(
        id=new_id(),
        cwd=str(tmp_path),
        model="test",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    newer = Thread(
        id=new_id(),
        cwd=str(tmp_path),
        model="test",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    store.create_thread(older)
    store.create_thread(newer)

    picked = store.find_latest_for_cwd(str(tmp_path))
    assert picked is not None
    assert picked.id == newer.id


def test_pick_thread_with_input_fn(tmp_path: Path) -> None:
    store_dir = tmp_path / "threads"
    store = ThreadStore(base_dir=store_dir)
    thread = Thread(id=new_id(), cwd=str(tmp_path.resolve()), model="test", title="alpha")
    store.create_thread(thread)
    store.rewrite_turns(thread)
    responses = iter(["1"])

    def fake_input(_prompt: str) -> str:
        return next(responses)

    picked = pick_thread(store, tmp_path, input_fn=fake_input)
    assert picked is not None
    assert picked.id == thread.id


def test_apply_last_dry_run(tmp_path: Path) -> None:
    patch_text = """*** Begin Patch
*** Add File: hello.txt
+line one
*** End Patch
"""
    thread = Thread(id="t-apply", cwd=str(tmp_path), model="test")
    thread.turns.append(
        Turn(
            status="completed",
            items=[
                FileChangeItem(
                    path="hello.txt",
                    status="completed",
                    tool_arguments=json.dumps({"patch": patch_text}),
                )
            ],
        )
    )
    result = apply_last_from_thread(thread, tmp_path, dry_run=True)
    assert result.ok
    assert result.preview_lines
    assert not (tmp_path / "hello.txt").exists()


def test_ephemeral_run_no_thread_file(tmp_path: Path) -> None:
    store = ThreadStore.ephemeral()
    thread = Thread(id=f"ephemeral-{new_id()}", cwd=str(tmp_path), model="test")
    store.create_thread(thread)
    assert not store.persistent
    assert list(default_store_dir().glob(f"{thread.id}.jsonl")) == []


def test_notify_fired_on_turn_complete(tmp_path: Path) -> None:
    settings = NotifySettings(command="echo notify-test", timeout_sec=2)
    with patch("agent.notify.subprocess.run") as mock_run:
        fire_notify(
            settings,
            thread_id="thread-1",
            turn_id="turn-1",
            status="completed",
            cwd=tmp_path,
        )
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["AGENT_THREAD_ID"] == "thread-1"
    assert kwargs["env"]["AGENT_STATUS"] == "completed"
    assert kwargs["env"]["AGENT_CWD"] == str(tmp_path)


def test_memory_scoring_prefers_matching_tags(tmp_path: Path) -> None:
    path = tmp_path / "memories.json"
    settings = MemoriesSettings(enabled=True, path=str(path))
    store = MemoryStore(path=path, settings=settings)
    store.add("generic note", tags=["docs"], cwd=str(tmp_path / "other"))
    store.add("pytest fixtures", tags=["pytest", "tests"], cwd=str(tmp_path))

    scored = store.search_scored("pytest fixtures", cwd=str(tmp_path))
    assert scored
    assert scored[0].memory.text == "pytest fixtures"
    assert scored[0].score >= scored[-1].score


def test_memory_inject_in_system_prompt(tmp_path: Path) -> None:
    path = tmp_path / "memories.json"
    settings = MemoriesSettings(enabled=True, path=str(path), max_inject=3)
    store = MemoryStore(path=path, settings=settings)
    store.add("Always run pytest after edits", tags=["pytest"], cwd=str(tmp_path))

    injected = inject_memories_prompt("fix pytest failure", settings, cwd=str(tmp_path))
    prompt = build_system_prompt(tmp_path, None, project_rules="", memories_text=injected)
    assert "Always run pytest after edits" in prompt


def test_memory_sandbox_allows_memories_path(tmp_path: Path) -> None:
    from agent.sandbox.enforcer import check_write_file
    from agent.sandbox.policy import SandboxMode

    settings = MemoriesSettings(enabled=True, path=str(tmp_path / "memories.json"))
    decision = check_write_file(
        str(tmp_path / "memories.json"),
        tmp_path,
        SandboxMode.WORKSPACE_WRITE,
        memories=settings,
    )
    assert decision.allowed


def test_apply_cli_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = tmp_path / "threads"
    store = ThreadStore(base_dir=store_dir)
    patch_text = """*** Begin Patch
*** Add File: note.txt
+hello
*** End Patch
"""
    thread = Thread(id=new_id(), cwd=str(tmp_path), model="test")
    thread.turns.append(
        Turn(
            status="completed",
            items=[
                FileChangeItem(
                    path="note.txt",
                    status="completed",
                    tool_arguments=json.dumps({"patch": patch_text}),
                )
            ],
        )
    )
    store.create_thread(thread)
    store.rewrite_turns(thread)
    monkeypatch.setattr("cli.main.ThreadStore", lambda: ThreadStore(base_dir=store_dir))
    result = runner.invoke(app, ["apply", "--thread-id", thread.id, "--dry-run", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "note.txt" in result.stdout or "Dry run" in result.stdout
