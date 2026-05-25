from __future__ import annotations

from agent.config import Config
from agent.events import EventEmitter
from agent.loop import _run_loop, run_turn
from agent.models import Thread, Turn
from agent.multi_agent.checkpoint import CheckpointStore, restore_registry
from agent.mcp.manager import McpManager
from agent.settings import load_mcp_config, load_skills_config
from agent.context import build_thread_messages, load_project_rules
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.store import ThreadStore
from agent.session import HarnessSession
from approval.gate import TurnApprovalState
from agent.cancel import CancelToken
from model.openrouter import OpenRouterClient
from tools.registry import get_tool_schemas


def resume_supervisor_turn(
    thread: Thread,
    checkpoint,
    config: Config,
    store: ThreadStore,
    *,
    retry_failed: bool = False,
    events: EventEmitter | None = None,
) -> Turn:
    """Resume a supervisor turn from checkpoint."""
    emitter = events or EventEmitter()
    turn = next((t for t in thread.turns if t.id == checkpoint.turn_id), None)
    if turn is None:
        turn = Turn(id=checkpoint.turn_id, status="running")
        thread.turns.append(turn)

    registry = restore_registry(
        config,
        store,
        checkpoint,
        emitter=emitter,
        parent_thread=thread,
        retry_failed=retry_failed,
    )
    if registry._pending_queue:
        registry._pump_queue(thread, checkpoint.turn_id)

    skills_cfg = load_skills_config(config.config_path)
    mcp_config = load_mcp_config(config.config_path, config.cwd)
    mcp_manager = McpManager(mcp_config)
    try:
        mcp_manager.connect_all()
    except Exception:
        pass

    messages = checkpoint.messages
    if not messages:
        messages = build_thread_messages(
            thread,
            execution_backend=config.execution.backend,
            sync_enabled=config.execution.ssh.sync_enabled,
        )

    client = OpenRouterClient(config)
    tools = get_tool_schemas(mcp_manager, config, allow_spawn=config.multi_agent.enabled)

    return _run_loop(
        thread=thread,
        turn=turn,
        config=config,
        store=store,
        client=client,
        tools=tools,
        messages=messages,
        emitter=emitter,
        cancel=CancelToken(),
        turn_state=TurnApprovalState(),
        session=HarnessSession(),
        on_text_delta=None,
        quiet_tools=False,
        mcp_manager=mcp_manager,
        active_skills=[],
        skills_max_body=skills_cfg.max_body_chars,
        project_rules="",
        worker_registry=registry,
        worker_depth=0,
        resume_messages=messages,
        resume_spawn_count=checkpoint.spawn_count,
        user_text=checkpoint.user_text,
    )


def list_checkpoint_status(thread_id: str, base_dir=None) -> list[dict]:
    store = CheckpointStore(base_dir)
    cps = store.list_for_thread(thread_id)
    return [
        {
            "turn_id": cp.turn_id,
            "status": cp.status,
            "spawn_count": cp.spawn_count,
            "workers": len(cp.workers),
            "completed": sum(1 for w in cp.workers if w.status == "completed"),
            "failed": sum(1 for w in cp.workers if w.status == "failed"),
        }
        for cp in cps
    ]
