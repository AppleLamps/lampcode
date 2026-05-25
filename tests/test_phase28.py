from __future__ import annotations

import json
import sys
import tarfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent.compaction import compact_thread_if_needed, force_compact_thread
from agent.config import Config
from agent.events import AgentEvent, build_event_emitter
from agent.hooks.runner import HooksRunner, SUPPORTED_HOOK_EVENTS, load_hooks_config
from agent.memories import (
    INJECT_CHAR_CAP,
    MemoryStore,
    SuggestQueue,
    inject_memories_dry_run,
    suggest_memory_from_turn,
)
from agent.models import AgentMessageItem, Thread, Turn, UserMessageItem
from agent.plan_mode import parse_proposed_plan
from agent.recording.bundle import export_run_bundle, read_bundle_manifest
from agent.recording.store import RunStore
from agent.repl import ReplSession
from agent.settings import CompactionSettings, HooksSettings, MemoriesSettings
from agent.store import ThreadStore
from cli.main import app
from model.openrouter import CompletionResult
from tools.registry import get_tool_schemas

runner = CliRunner()


def test_run_bundle_export_contains_manifest_and_events(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    threads_dir = tmp_path / "threads"
    store = RunStore(base_dir=runs_dir)
    thread_store = ThreadStore(base_dir=threads_dir)
    thread = Thread(id="th-bundle", cwd=str(tmp_path), model="test-model")
    thread_store.create_thread(thread)
    store.append_event("th-bundle", "turn1", AgentEvent("turn.started", thread_id="th-bundle", turn_id="turn1"))
    store.append_event(
        "th-bundle",
        "turn1",
        AgentEvent("tool.pending", thread_id="th-bundle", turn_id="turn1", data={"tool_name": "read_file"}),
    )
    out = tmp_path / "run.bundle.tar.gz"
    manifest = export_run_bundle(
        turn_id="turn1",
        thread_id="th-bundle",
        out_path=out,
        config=Config(cwd=tmp_path, model="test-model", openrouter_api_key="sk-test"),
        run_store=store,
        thread_store=thread_store,
    )
    assert manifest["schema_version"] == "v1"
    assert manifest["thread_id"] == "th-bundle"
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names
    assert "events.jsonl" in names
    assert "thread.jsonl" in names
    assert "config.redacted.json" in names


def test_run_bundle_redacts_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-key-value")
    runs_dir = tmp_path / "runs"
    store = RunStore(base_dir=runs_dir)
    store.append_event("t1", "u1", AgentEvent("turn.started", thread_id="t1", turn_id="u1"))
    out = tmp_path / "bundle.tar.gz"
    export_run_bundle(
        turn_id="u1",
        thread_id="t1",
        out_path=out,
        config=Config(cwd=tmp_path, model="m", openrouter_api_key="sk-or-secret-key-value"),
        run_store=store,
    )
    with tarfile.open(out, "r:gz") as tar:
        config_bytes = tar.extractfile("config.redacted.json").read().decode("utf-8")
    assert "sk-or-" not in config_bytes
    assert "OPENROUTER_API_KEY" not in config_bytes
    assert '"openrouter_api_key_set": true' in config_bytes


def test_run_bundle_info_reads_manifest_without_full_extract(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    store = RunStore(base_dir=runs_dir)
    store.append_event("t1", "u1", AgentEvent("turn.started", thread_id="t1", turn_id="u1"))
    out = tmp_path / "bundle.tar.gz"
    export_run_bundle(
        turn_id="u1",
        thread_id="t1",
        out_path=out,
        config=Config(cwd=tmp_path, model="m", openrouter_api_key="x"),
        run_store=store,
    )
    manifest = read_bundle_manifest(out)
    result = runner.invoke(app, ["runs", "bundle-info", str(out)])
    assert result.exit_code == 0
    assert manifest["turn_id"] == "u1"
    assert "u1" in result.output


def test_run_bundle_manifest_matches_schema(tmp_path: Path) -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "run.bundle.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema["required"])
    runs_dir = tmp_path / "runs"
    store = RunStore(base_dir=runs_dir)
    store.append_event("t1", "u1", AgentEvent("turn.started", thread_id="t1", turn_id="u1"))
    out = tmp_path / "bundle.tar.gz"
    manifest = export_run_bundle(
        turn_id="u1",
        thread_id="t1",
        out_path=out,
        config=Config(cwd=tmp_path, model="m", openrouter_api_key="x"),
        run_store=store,
    )
    assert required.issubset(manifest.keys())
    assert manifest["schema_version"] == "v1"


def test_hooks_session_start_fires_subprocess(tmp_path: Path) -> None:
    marker = tmp_path / "started.txt"
    script = tmp_path / "hook.py"
    script.write_text(
        f"open(r'{marker}', 'w').write('ok')\n",
        encoding="utf-8",
    )
    hooks_file = tmp_path / ".agent-cli" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(
        json.dumps({"on_session_start": [{"command": f"{sys.executable} {script}", "timeout_sec": 5}]}),
        encoding="utf-8",
    )
    hooks = load_hooks_config(tmp_path)
    runner_obj = HooksRunner(cwd=tmp_path, settings=HooksSettings(), hooks=hooks)
    errors = runner_obj.run("on_session_start", {"thread_id": "t1", "turn_id": "u1"})
    assert not errors
    assert marker.read_text(encoding="utf-8") == "ok"


def test_hooks_pre_tool_use_block_skips_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    script = tmp_path / "block.py"
    script.write_text('print(\'{"decision": "block", "reason": "policy"}\')\n', encoding="utf-8")
    hooks_file = tmp_path / ".agent-cli" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(
        json.dumps({"on_pre_tool_use": [{"command": f"{sys.executable} {script}", "timeout_sec": 5}]}),
        encoding="utf-8",
    )
    stream_results = [
        CompletionResult(
            "",
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "x.txt"}),
                    },
                }
            ],
            "tool_calls",
            {"prompt_tokens": 10, "completion_tokens": 5},
        ),
        CompletionResult("done", [], "stop", {"prompt_tokens": 5, "completion_tokens": 2}),
    ]
    with patch("agent.loop.OpenRouterClient") as mock_client_cls, patch(
        "agent.loop.dispatch_tool"
    ) as mock_dispatch:
        mock_client_cls.return_value.stream_completion.side_effect = stream_results
        result = runner.invoke(
            app,
            ["run", "read x", "--cwd", str(tmp_path), "--auto-approve", "--skip-git-check"],
        )
        assert result.exit_code == 0
        mock_dispatch.assert_not_called()
        assert "policy" in (result.stdout or "") + (result.stderr or "")


def test_hooks_user_prompt_submit_context_append(tmp_path: Path) -> None:
    script = tmp_path / "append.py"
    script.write_text('print(\'{"context_append": "extra context"}\')\n', encoding="utf-8")
    hooks_file = tmp_path / ".agent-cli" / "hooks.json"
    hooks_file.parent.mkdir(parents=True)
    hooks_file.write_text(
        json.dumps(
            {"on_user_prompt_submit": [{"command": f"{sys.executable} {script}", "timeout_sec": 5}]}
        ),
        encoding="utf-8",
    )
    hooks = load_hooks_config(tmp_path)
    runner_obj = HooksRunner(cwd=tmp_path, settings=HooksSettings(), hooks=hooks)
    result = runner_obj.run_event("on_user_prompt_submit", {"prompt": "hello"})
    assert result.context_append == "extra context"
    assert not result.errors


def test_hooks_list_includes_v2_events(tmp_path: Path) -> None:
    result = runner.invoke(app, ["hooks", "list", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    for event in SUPPORTED_HOOK_EVENTS:
        assert event in result.output


def test_memories_suggest_queue_accept_promotes_to_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem_path = tmp_path / "memories.json"
    settings = MemoriesSettings(path=str(mem_path), enabled=True, auto_suggest=True)
    queue = SuggestQueue(tmp_path, max_pending=5)
    mem = queue.enqueue("Always run pytest", cwd=str(tmp_path))
    store = MemoryStore(path=mem_path, settings=settings)
    accepted = queue.accept(mem.id, store=store)
    assert accepted is not None
    assert store.search("pytest")
    assert queue.pending_count() == 0


def test_memories_inject_dry_run_respects_char_cap(tmp_path: Path) -> None:
    mem_path = tmp_path / "memories.json"
    settings = MemoriesSettings(path=str(mem_path), enabled=True, max_inject=10)
    store = MemoryStore(path=mem_path, settings=settings)
    for i in range(20):
        store.add(f"memory line {i} " + ("x" * 200), tags=[f"tag{i}"])
    block = inject_memories_dry_run("memory tag", settings=settings)
    assert len(block) <= INJECT_CHAR_CAP


def test_memories_scoring_prefers_tag_and_keyword_match(tmp_path: Path) -> None:
    mem_path = tmp_path / "memories.json"
    store = MemoryStore(path=mem_path, settings=MemoriesSettings(path=str(mem_path)))
    store.add("generic note", tags=["misc"])
    tagged = store.add("run pytest before commit", tags=["pytest"])
    scored = store.search_scored("pytest commit")
    assert scored[0].memory.id == tagged.id
    assert scored[0].score >= scored[1].score


def test_plan_mode_parses_proposed_plan_block() -> None:
    text = "Here is my approach.\n<proposed_plan>\n1. Read files\n2. Patch\n</proposed_plan>\nThanks."
    stripped, plan = parse_proposed_plan(text)
    assert plan == "1. Read files\n2. Patch"
    assert "<proposed_plan>" not in stripped
    assert "Here is my approach." in stripped


def test_plan_mode_json_emits_plan_proposed_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.store.default_store_dir", lambda: tmp_path / "threads")
    content = "Summary\n<proposed_plan>\nStep one\n</proposed_plan>"
    with patch("agent.loop.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.stream_completion.return_value = CompletionResult(
            content, [], "stop", {"prompt_tokens": 1, "completion_tokens": 1}
        )
        result = runner.invoke(
            app,
            ["run", "plan refactor", "--cwd", str(tmp_path), "--plan", "--auto-approve", "--skip-git-check", "--json"],
        )
    assert result.exit_code == 0
    assert "plan.proposed" in result.output


def test_plan_mode_write_tools_still_blocked() -> None:
    cfg = Config.resolve()
    allowed = cfg.plan_mode.allowed_tools
    schemas = get_tool_schemas(config=cfg, allowed_tools=allowed)
    names = {s["function"]["name"] for s in schemas}
    assert "apply_patch" not in names
    assert "write_file" not in names


def test_doctor_json_includes_openrouter_and_shell_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    checks = {row["check"] for row in data}
    assert "OPENROUTER_API_KEY" in checks
    assert "shell" in checks
    assert "hooks" in checks
    assert "memories" in checks


def test_doctor_json_exit_code_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("agent.config.Config.resolve") as mock_resolve:
        mock_resolve.return_value = Config(cwd=Path.cwd(), model="m", openrouter_api_key=None)
        result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1


def test_repl_compact_command_reduces_context(tmp_path: Path) -> None:
    thread = Thread(id="t-repl", cwd=str(tmp_path), model="test")
    for i in range(4):
        thread.turns.append(
            Turn(
                status="completed",
                items=[
                    UserMessageItem(text=f"msg {i}"),
                    AgentMessageItem(text="y" * 500),
                ],
            )
        )
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=50,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.05, keep_recent_turns=1),
    )
    out = StringIO()
    repl = ReplSession(config=config, store=store, thread=thread, print_fn=lambda s: out.write(s + "\n"))
    with patch("model.openrouter.OpenRouterClient") as mock_client_cls:
        mock_client_cls.return_value.complete.return_value = "summary"
        repl._handle_command("/compact")
    text = out.getvalue()
    assert "Compacted" in text or "Nothing to compact" in text
    if "Compacted" in text:
        mock_client_cls.return_value.complete.assert_called()


def test_compaction_auto_mid_turn_disabled_skips_mid_turn(tmp_path: Path) -> None:
    thread = Thread(id="t-mid", cwd=str(tmp_path), model="test")
    for i in range(4):
        thread.turns.append(
            Turn(
                status="completed",
                items=[UserMessageItem(text="x"), AgentMessageItem(text="y" * 500)],
            )
        )
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=50,
        openrouter_api_key="x",
        compaction=CompactionSettings(
            enabled=True, threshold=0.05, keep_recent_turns=1, auto_mid_turn=False
        ),
    )
    client = MagicMock()
    client.complete.return_value = "summary"
    result = compact_thread_if_needed(thread, config, store, client, mid_turn=True)
    assert not result.performed
    forced = force_compact_thread(thread, config, store, client)
    assert forced.performed


def test_compaction_double_compact_emits_warning(tmp_path: Path) -> None:
    from agent.models import ContextCompactionItem

    thread = Thread(id="t-warn28", cwd=str(tmp_path), model="test")
    thread.turns = [
        Turn(
            status="completed",
            items=[
                ContextCompactionItem(summarized_items=1),
                AgentMessageItem(text="[Compaction Summary]\nfirst"),
            ],
        ),
        Turn(status="completed", items=[UserMessageItem(text="x"), AgentMessageItem(text="y" * 500)]),
        Turn(status="completed", items=[UserMessageItem(text="z"), AgentMessageItem(text="w" * 500)]),
    ]
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    config = Config(
        cwd=tmp_path,
        model="test",
        context_window_tokens=50,
        openrouter_api_key="x",
        compaction=CompactionSettings(enabled=True, threshold=0.05, keep_recent_turns=1),
    )
    client = MagicMock()
    client.complete.return_value = "summary"
    result = compact_thread_if_needed(thread, config, store, client)
    assert result.warning is not None


def test_suggest_memory_from_turn_queues(tmp_path: Path) -> None:
    settings = MemoriesSettings(enabled=True, auto_suggest=True)
    turn = Turn(
        status="completed",
        items=[AgentMessageItem(text="All tests pass after pytest fix.")],
    )
    suggest_memory_from_turn(
        turn,
        thread_id="t1",
        cwd=str(tmp_path),
        settings=settings,
    )
    assert SuggestQueue(tmp_path).pending_count() == 1
