"""Turn lifecycle: setup, run state machine, teardown."""
from __future__ import annotations

from typing import Callable

from agent.cancel import CancelToken, CancelledError
from agent.config import Config
from agent.context import build_thread_messages, load_project_rules
from agent.events import EventEmitter
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.mcp.manager import McpManager
from agent.models import (
    AgentMessageItem,
    SkillActivationItem,
    Thread,
    Turn,
    UserMessageItem,
    WorkspaceSyncItem,
)
from agent.multi_agent.checkpoint import save_checkpoint_from_registry
from agent.multi_agent.registry import WorkerRegistry
from agent.execution.sync.service import maybe_sync_turn_end, maybe_sync_turn_start
from agent.profiles import project_config_path
from agent.session import HarnessSession
from agent.settings import load_mcp_config, load_skills_config
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.telemetry import init_telemetry, trace_span
from agent.turn.finalize import finalize_cancelled
from agent.turn.state_machine import run_loop
from agent.turn_checkpoint import TurnCheckpoint, clear_turn_checkpoint, save_turn_checkpoint
from agent.store import ThreadStore
from approval.gate import TurnApprovalState, prompt_approval, prompt_sync_conflict
import agent.loop as loop_shim

def run_turn(
    thread: Thread,
    user_text: str,
    config: Config,
    store: ThreadStore,
    *,
    on_text_delta: Callable[[str], None] | None = None,
    events: EventEmitter | None = None,
    cancel_token: CancelToken | None = None,
    quiet_tools: bool = False,
    harness_session: HarnessSession | None = None,
    session_auto_approve: bool = False,
    worker_registry: WorkerRegistry | None = None,
    worker_depth: int = 0,
    force_sync: bool = False,
    resume_messages: list | None = None,
    resume_spawn_count: int = 0,
    budget_profile: str | None = None,
    resume_checkpoint: TurnCheckpoint | None = None,
    allowed_tools: list[str] | None = None,
    system_prompt_append: str = "",
    read_only_review: bool = False,
    plan_mode: bool = False,
    headless_json: bool = False,
    output_schema: dict | None = None,
    thread_title: str | None = None,
    remember: bool = False,
) -> Turn:
    emitter = events or EventEmitter()
    cancel = cancel_token or CancelToken()
    init_telemetry(config)
    turn_state = TurnApprovalState()
    session = harness_session or HarnessSession()
    if session_auto_approve:
        session.enable_session_auto_approve()

    skills_cfg = load_skills_config(config.config_path)
    mcp_config = load_mcp_config(config.config_path, config.cwd)
    mcp_manager = McpManager(mcp_config)

    turn = Turn()
    resumed_turn = False
    if resume_checkpoint:
        existing = next((t for t in thread.turns if t.id == resume_checkpoint.turn_id), None)
        if existing is None:
            turn.id = resume_checkpoint.turn_id
            thread.turns.append(turn)
        else:
            turn = existing
            resumed_turn = True
        user_text = resume_checkpoint.user_text or user_text
        resume_messages = resume_checkpoint.messages
        if (
            turn.items
            and turn.items[-1].type == "agentMessage"
            and "cancelled" in turn.items[-1].text.lower()
        ):
            turn.items.pop()
        turn.status = "running"
    else:
        thread.turns.append(turn)

    if not resumed_turn:
        emitter.turn_started(thread.id, turn.id)
    else:
        emitter.turn_started(thread.id, turn.id)
    MetricsCollector.global_collector().inc("turns_started")
    MetricsCollector.global_collector().adjust_gauge("active_turns", 1)
    ActiveTurnRegistry.global_registry().register(thread.id, turn.id, cancel)

    if not resumed_turn:
        user_item = UserMessageItem(text=user_text)
        turn.items.append(user_item)
        store.append_item(thread, turn.id, user_item)
        emitter.user_message(thread.id, turn.id, user_text)

    all_skills = discover_skills(
        config.cwd,
        enable_project=skills_cfg.enable_project_skills,
        enable_user=skills_cfg.enable_user_skills,
    )
    active_skills = select_skills(
        all_skills, user_text, max_active=skills_cfg.max_active
    )
    if active_skills:
        skill_item = SkillActivationItem(skills=[s.name for s in active_skills])
        turn.items.append(skill_item)
        store.append_item(thread, turn.id, skill_item)
        emitter.skill_activation(thread.id, turn.id, skill_item.skills)

    rules_text, rules_meta = load_project_rules(
        config.cwd, max_chars=skills_cfg.project_rules_max_chars
    )
    if rules_meta:
        emitter.project_rules_loaded(
            thread.id, str(rules_meta.path), rules_meta.char_count
        )

    try:
        mcp_manager.connect_all(
            on_connected=lambda s: emitter.mcp_server_connected(thread.id, s),
            on_failed=lambda s, e: emitter.mcp_server_failed(thread.id, s, e),
        )
    except Exception:
        pass

    if thread_title:
        thread.title = thread_title
        store.save_thread(thread)

    from agent.hooks.runner import HooksRunner
    from agent.memories import (
        extract_memory_from_message,
        inject_memories_prompt,
        suggest_memory_from_turn,
    )
    from agent.notify import fire_notify

    hooks_runner = HooksRunner.from_cwd(
        config.cwd,
        config_path=config.config_path,
        project_path=project_config_path(config.cwd) if config.cwd else None,
        emitter=emitter,
    )
    hooks_runner.thread_id = thread.id
    hooks_runner.turn_id = turn.id
    hooks_runner.run_event(
        "on_session_start",
        {
            "thread_id": thread.id,
            "turn_id": turn.id,
            "cwd": str(config.cwd),
            "model": config.model,
        },
        thread_id=thread.id,
        turn_id=turn.id,
    )

    effective_allowed = allowed_tools
    effective_allow_mcp: list[str] | None = None
    if plan_mode:
        effective_allowed = list(config.plan_mode.allowed_tools)
        effective_allow_mcp = list(config.plan_mode.allow_mcp_servers)
        from agent.plan_mode import build_plan_system_append

        plan_append = build_plan_system_append(
            effective_allowed,
            allow_mcp_servers=effective_allow_mcp,
        )
        system_prompt_append = f"{system_prompt_append}\n{plan_append}".strip()
    memories_text = (
        inject_memories_prompt(user_text, config.memories, cwd=str(config.cwd))
        if config.memories.enabled
        else ""
    )

    prompt_hook = hooks_runner.run_event(
        "on_user_prompt_submit",
        {"thread_id": thread.id, "turn_id": turn.id, "prompt": user_text},
        thread_id=thread.id,
        turn_id=turn.id,
    )
    if prompt_hook.context_append:
        system_prompt_append = f"{system_prompt_append}\n{prompt_hook.context_append}".strip()

    from agent.context import build_lsp_mcp_append

    lsp_append = build_lsp_mcp_append(mcp_manager)
    if lsp_append:
        system_prompt_append = f"{system_prompt_append}\n{lsp_append}".strip()

    client = loop_shim.OpenRouterClient(config)
    tools = loop_shim.get_tool_schemas(
        mcp_manager,
        config,
        allow_spawn=config.multi_agent.enabled,
        allowed_tools=effective_allowed,
        allow_mcp_servers=effective_allow_mcp,
    )
    if not session.tool_warn_emitted:
        from agent.providers.openrouter import ModelsCache, tool_support_warning

        models_cache = ModelsCache().load()
        tool_warn = tool_support_warning(config.model, models_cache or None)
        if tool_warn:
            emitter.error(thread.id, tool_warn)
        session.tool_warn_emitted = True
    budget_tracker = None
    if config.multi_agent.budgets.enabled or (budget_profile and budget_profile.lower() != "off"):
        from agent.multi_agent.budgets import SwarmBudgetTracker, profile_settings

        if budget_profile:
            bsettings = profile_settings(budget_profile, config.multi_agent.budgets)
        else:
            bsettings = config.multi_agent.budgets
        if bsettings.enabled:
            budget_tracker = SwarmBudgetTracker(bsettings, model=config.model)
    messages = build_thread_messages(
        thread,
        active_skills=active_skills,
        skills_max_body=skills_cfg.max_body_chars,
        project_rules=rules_text,
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
        memories_text=memories_text,
        system_prompt_append=system_prompt_append,
    )

    def _approve_sync() -> bool:
        summary = f"sync push: {config.cwd} -> {config.execution.ssh.remote_workspace}"
        emitter.approval_requested(thread.id, turn.id, "sync_push", summary)
        return prompt_approval(
            "sync_push",
            {"direction": "push"},
            auto_approve=config.auto_approve,
            turn_state=turn_state,
            session=session,
        )

    def _resolve_sync_conflict(rel: str) -> str | None:
        summary = f"sync conflict: {rel} [l=local-wins / r=remote-wins / s=skip / a=abort]"
        emitter.approval_requested(thread.id, turn.id, "sync_conflict", summary)
        approved = prompt_sync_conflict(
            rel,
            auto_approve=config.auto_approve,
            turn_state=turn_state,
            session=session,
        )
        return approved

    sync_result, sync_item = maybe_sync_turn_start(
        config,
        session,
        force=force_sync,
        approve_fn=_approve_sync,
        conflict_fn=_resolve_sync_conflict,
        emitter=emitter,
        thread_id=thread.id,
        turn_id=turn.id,
    )
    if sync_item:
        turn.items.append(sync_item)
        store.append_item(thread, turn.id, sync_item)
        if sync_result and not sync_result.ok and sync_result.error:
            turn.status = "failed"
            fail_item = AgentMessageItem(text=f"Sync push failed: {sync_result.error}")
            turn.items.append(fail_item)
            store.append_item(thread, turn.id, fail_item)
            store.append_turn(thread, turn)
            emitter.turn_completed(thread.id, turn.id, turn.status)
            return turn

    try:
        registry = worker_registry
        if registry is None and config.multi_agent.enabled:
            registry = WorkerRegistry(
                config,
                store,
                emitter=emitter,
                parent_thread=thread,
                turn_id=turn.id,
                depth=worker_depth,
            )
        with trace_span(
            "turn.run",
            thread_id=thread.id,
            turn_id=turn.id,
            backend=config.execution.backend,
        ):
            completed_turn = run_loop(
                thread=thread,
                turn=turn,
                config=config,
                store=store,
                client=client,
                tools=tools,
                messages=messages,
                emitter=emitter,
                cancel=cancel,
                turn_state=turn_state,
                session=session,
                on_text_delta=on_text_delta,
                quiet_tools=quiet_tools,
                mcp_manager=mcp_manager,
                active_skills=active_skills,
                skills_max_body=skills_cfg.max_body_chars,
                project_rules=rules_text,
                worker_registry=registry,
                worker_depth=worker_depth,
                force_sync=force_sync,
                resume_messages=resume_messages,
                resume_spawn_count=resume_spawn_count,
                user_text=user_text,
                budget_tracker=budget_tracker,
                allowed_tools=effective_allowed,
                allow_mcp_servers=effective_allow_mcp,
                read_only_review=read_only_review,
                plan_mode=plan_mode,
                hooks_runner=hooks_runner,
                headless_json=headless_json,
                output_schema=output_schema,
                memories_text=memories_text,
                system_prompt_append=system_prompt_append,
            )
        pull_result, pull_item = maybe_sync_turn_end(
            config,
            completed_turn.status,
            emitter=emitter,
            thread_id=thread.id,
            turn_id=turn.id,
        )
        if pull_item:
            completed_turn.items.append(pull_item)
            store.append_item(thread, turn.id, pull_item)
        clear_turn_checkpoint(config.turn_checkpoint, thread.id, turn.id)
        if config.memories.enabled:
            for item in reversed(completed_turn.items):
                if item.type == "agentMessage":
                    extract_memory_from_message(
                        item.text,
                        thread_id=thread.id,
                        cwd=str(config.cwd),
                    )
                    break
            if completed_turn.status == "completed":
                suggest_memory_from_turn(
                    completed_turn,
                    thread_id=thread.id,
                    cwd=str(config.cwd),
                    settings=config.memories,
                    auto_approve=config.auto_approve,
                    remember_flag=remember,
                )
        session.clear_turn_escalations()
        if config.shell.enabled:
            from agent.execution.shell_session import ShellSessionManager

            ShellSessionManager.global_manager().close_thread(thread.id)
        hooks_runner.run(
            "on_turn_completed",
            {"status": completed_turn.status, "turn_id": completed_turn.id},
            thread_id=thread.id,
            turn_id=completed_turn.id,
        )
        fire_notify(
            config.notify,
            thread_id=thread.id,
            turn_id=completed_turn.id,
            status=completed_turn.status,
            cwd=config.cwd,
        )
        return completed_turn
    except CancelledError:
        save_turn_checkpoint(
            settings=config.turn_checkpoint,
            thread_id=thread.id,
            turn_id=turn.id,
            user_text=user_text,
            messages=messages,
            status="cancelled",
        )
        if registry and config.multi_agent.checkpoint_enabled:
            path = save_checkpoint_from_registry(
                registry,
                config,
                thread_id=thread.id,
                turn_id=turn.id,
                spawn_count=registry.spawn_count if hasattr(registry, "spawn_count") else 0,
                messages=messages,
                status="cancelled",
                user_text=user_text,
            )
            if path and emitter:
                emitter.collab_checkpoint_saved(thread.id, turn.id, path=str(path))
        finalize_cancelled(thread, turn, store, emitter, config=config)
        raise
    finally:
        ActiveTurnRegistry.global_registry().unregister(thread.id)
        MetricsCollector.global_collector().adjust_gauge("active_turns", -1)
        try:
            mcp_manager.disconnect_all()
        except Exception:
            pass
        try:
            session.clear_turn_escalations()
        except Exception:
            pass
