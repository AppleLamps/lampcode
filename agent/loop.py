from __future__ import annotations

import json
from typing import Any, Callable

from agent.cancel import CancelToken, CancelledError
from agent.compaction import compact_thread_if_needed, compact_tool_outputs
from agent.context_meter import (
    build_context_snapshot,
    extract_api_context_tokens,
    invalidate_context_cache,
)
from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens, load_project_rules
from agent.events import EventEmitter
from agent.sandbox.retry import apply_sandbox_escalation_for_reason
from agent.tool_round import can_parallelize_tool_round, run_parallel_tool_dispatches
from agent.profiles import project_config_path
from agent.mcp.manager import McpManager
from agent.models import (
    AgentMessageItem,
    CollabSpawnItem,
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    SkillActivationItem,
    Thread,
    Turn,
    UserInputItem,
    UserMessageItem,
    WebSearchItem,
    WorkspaceSyncItem,
)
from agent.harness.active_turns import ActiveTurnRegistry
from agent.telemetry import init_telemetry, trace_span
from agent.metrics import MetricsCollector
from agent.multi_agent.checkpoint import save_checkpoint_from_registry
from agent.multi_agent.registry import WorkerRegistry
from agent.execution.factory import backend_display
from agent.execution.sync.service import maybe_sync_turn_end, maybe_sync_turn_start
from agent.session import HarnessSession
from agent.settings import load_mcp_config, load_skills_config
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.turn_checkpoint import (
    TurnCheckpoint,
    clear_turn_checkpoint,
    save_turn_checkpoint,
)
from approval.gate import (
    TurnApprovalState,
    exec_policy_block_reason,
    format_tool_summary,
    needs_approval_prompt,
    prompt_approval,
    prompt_sync_conflict,
)
from agent.sandbox.enforcer import (
    check_apply_patch,
    check_mcp_tool,
    check_run_command,
    check_write_file,
)
from model.openrouter import OpenRouterClient, OpenRouterError


def _approval_diff_preview_for_tool(
    tool_name: str, arguments: dict, config: Config
) -> str | None:
    if tool_name != "apply_patch":
        return None
    from agent.tui.patch_preview import patch_approval_diff_preview

    return patch_approval_diff_preview(arguments, cwd=config.cwd)
from tools.registry import (
    DispatchResult,
    dispatch_tool,
    get_tool_schemas,
    parse_tool_arguments,
    tool_requires_approval,
)

TrackingItem = (
    CommandExecutionItem
    | FileChangeItem
    | McpToolCallItem
    | WebSearchItem
    | CollabSpawnItem
    | CollabWorkerItem
)


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

    client = OpenRouterClient(config)
    tools = get_tool_schemas(
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
            completed_turn = _run_loop(
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
        _finalize_cancelled(thread, turn, store, emitter, config=config)
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


def _run_loop(
    *,
    thread: Thread,
    turn: Turn,
    config: Config,
    store: ThreadStore,
    client: OpenRouterClient,
    tools: list,
    messages: list,
    emitter: EventEmitter,
    cancel: CancelToken,
    turn_state: TurnApprovalState,
    session: HarnessSession,
    on_text_delta: Callable[[str], None] | None,
    quiet_tools: bool,
    mcp_manager: McpManager,
    active_skills: list,
    skills_max_body: int,
    project_rules: str,
    worker_registry: WorkerRegistry | None = None,
    worker_depth: int = 0,
    force_sync: bool = False,
    resume_messages: list | None = None,
    resume_spawn_count: int = 0,
    user_text: str = "",
    budget_tracker: "SwarmBudgetTracker | None" = None,
    allowed_tools: list[str] | None = None,
    allow_mcp_servers: list[str] | None = None,
    read_only_review: bool = False,
    plan_mode: bool = False,
    hooks_runner: "HooksRunner | None" = None,
    headless_json: bool = False,
    output_schema: dict | None = None,
    memories_text: str = "",
    system_prompt_append: str = "",
) -> Turn:
    spawn_count = resume_spawn_count
    if resume_messages:
        messages = list(resume_messages)
    backend_announced = False
    registry = worker_registry
    if registry:
        registry.spawn_count = spawn_count
    budget = budget_tracker
    context_length_retried = False
    for _round in range(config.max_rounds):
        cancel.check()
        if budget:
            metric = budget.tick_wall_clock()
            if metric and budget.should_kill():
                return _budget_kill_turn(
                    thread, turn, store, emitter, registry, budget, metric, cancel
                )

        compact_result = compact_thread_if_needed(
            thread,
            config,
            store,
            client,
            hooks_runner=hooks_runner,
            project_rules=project_rules,
        )
        if compact_result.performed:
            invalidate_context_cache(thread.id)
            emitter.compaction(thread.id, compact_result.removed_items)
            emitter.compaction_completed(
                thread.id,
                removed_items=compact_result.removed_items,
                summary_chars=compact_result.summary_chars,
                estimated_tokens_before=compact_result.estimated_tokens_before,
                estimated_tokens_after=compact_result.estimated_tokens_after,
            )
            if compact_result.warning:
                emitter.compaction_warning(thread.id, compact_result.warning)
            messages = build_thread_messages(
                thread,
                active_skills=active_skills,
                skills_max_body=skills_max_body,
                project_rules=project_rules,
                execution_backend=config.execution.backend,
                sync_enabled=config.execution.ssh.sync_enabled,
                memories_text=memories_text,
                system_prompt_append=system_prompt_append,
            )

        def delta_handler(text: str) -> None:
            cancel.check()
            emitter.agent_delta(thread.id, turn.id, text)
            if on_text_delta:
                on_text_delta(text)

        response_format = None
        if output_schema:
            from agent.providers.openrouter import (
                ModelsCache,
                build_response_format,
                model_supports_structured_outputs,
            )

            cached = ModelsCache().load()
            if model_supports_structured_outputs(config.model, cached or None):
                response_format = build_response_format(output_schema)

        guard_messages = _run_pre_turn_context_guard(
            thread,
            config,
            store,
            client,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
            hooks_runner=hooks_runner,
        )
        if guard_messages is not None:
            messages = guard_messages

        try:
            result = client.stream_completion(
                messages,
                tools=tools,
                on_delta=delta_handler,
                cancel_token=cancel,
                response_format=response_format,
            )
        except CancelledError:
            raise
        except OpenRouterError as exc:
            if (
                getattr(exc, "error_kind", None) == "context_length"
                and not context_length_retried
            ):
                context_length_retried = True
                compact_result = compact_thread_if_needed(
                    thread,
                    config,
                    store,
                    client,
                    hooks_runner=hooks_runner,
                    project_rules=project_rules,
                    force=True,
                )
                if compact_result.performed:
                    invalidate_context_cache(thread.id)
                    messages = build_thread_messages(
                        thread,
                        active_skills=active_skills,
                        skills_max_body=skills_max_body,
                        project_rules=project_rules,
                        execution_backend=config.execution.backend,
                        sync_enabled=config.execution.ssh.sync_enabled,
                        memories_text=memories_text,
                        system_prompt_append=system_prompt_append,
                    )
                    continue
            turn.status = "failed"
            emitter.error(thread.id, str(exc))
            store.append_turn(thread, turn)
            raise

        if result.usage:
            turn.usage.input_tokens = result.usage.get("prompt_tokens")
            turn.usage.output_tokens = result.usage.get("completion_tokens")
        from agent.providers.openrouter import enrich_usage

        enriched = enrich_usage(
            config,
            model_used=result.model_used or config.model,
            fallback_used=result.fallback_used,
            usage=result.usage,
        )
        session.turn_cost_usd += float(enriched.get("estimated_cost_usd") or 0.0)
        turn.usage.input_tokens = (turn.usage.input_tokens or 0) + int(enriched.get("input_tokens") or 0)
        turn.usage.output_tokens = (turn.usage.output_tokens or 0) + int(enriched.get("output_tokens") or 0)
        turn.usage.estimated_cost_usd = round(session.turn_cost_usd, 6)
        turn.usage.model_used = enriched.get("model_used")
        turn.usage.fallback_used = bool(enriched.get("fallback_used"))
        ctx_tokens = extract_api_context_tokens(result.usage)
        if ctx_tokens is not None:
            turn.usage.context_tokens = ctx_tokens
            thread.last_context_tokens = ctx_tokens
            thread.last_context_model = enriched.get("model_used") or config.model
            store.save_thread(thread)
        if config.max_cost_usd_per_turn and session.turn_cost_usd > config.max_cost_usd_per_turn:
            session.budget_exceeded = True
            return _cost_cap_kill_turn(
                thread,
                turn,
                store,
                emitter,
                config.max_cost_usd_per_turn,
                session.turn_cost_usd,
            )
        if budget:
            metric = budget.record_usage(result.usage)
            if metric and budget.should_kill():
                return _budget_kill_turn(
                    thread, turn, store, emitter, registry, budget, metric, cancel
                )

        if not result.tool_calls:
            display_text = result.content
            if plan_mode:
                from agent.plan_mode import parse_proposed_plan, plan_summary

                display_text, plan_body = parse_proposed_plan(result.content or "")
                if plan_body:
                    from agent.models import PlanProposalItem

                    plan_item = PlanProposalItem(text=plan_body)
                    turn.items.append(plan_item)
                    store.append_item(thread, turn.id, plan_item)
                    emitter.plan_proposed(
                        thread.id,
                        turn.id,
                        text=plan_body,
                        summary=plan_summary(plan_body),
                    )
            agent_item = AgentMessageItem(text=display_text)
            turn.items.append(agent_item)
            store.append_item(thread, turn.id, agent_item)
            turn.status = "completed"
            store.append_turn(thread, turn)
            store.save_thread(thread)
            if budget:
                from agent.multi_agent.budget_state import save_budget_state

                save_budget_state(thread.id, budget.to_dict())
            emitter.turn_completed(
                thread.id, turn.id, turn.status, estimated_tokens=estimate_tokens(messages)
            )
            return turn

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": result.tool_calls,
        }
        reasoning = getattr(result, "reasoning", "") or ""
        reasoning_details = getattr(result, "reasoning_details", None) or []
        if reasoning:
            assistant_msg["reasoning"] = reasoning
            emitter.agent_reasoning(thread.id, turn.id, reasoning)
        if reasoning_details:
            assistant_msg["reasoning_details"] = reasoning_details
        messages.append(assistant_msg)

        tool_calls_list = list(result.tool_calls)
        tool_names_round = [tc["function"]["name"] for tc in tool_calls_list]
        if (
            len(tool_calls_list) > 1
            and can_parallelize_tool_round(tool_names_round)
            and config.harness.max_parallel_read_tools > 1
        ):
            _run_parallel_read_tool_round(
                tool_calls_list,
                thread=thread,
                turn=turn,
                config=config,
                store=store,
                mcp_manager=mcp_manager,
                messages=messages,
                emitter=emitter,
                cancel=cancel,
                allowed_tools=allowed_tools,
                allow_mcp_servers=allow_mcp_servers,
                turn_state=turn_state,
                session=session,
                hooks_runner=hooks_runner,
                max_workers=config.harness.max_parallel_read_tools,
            )
            continue

        for tc in tool_calls_list:
            cancel.check()
            if budget:
                metric = budget.record_tool_call()
                if metric and budget.should_kill():
                    return _budget_kill_turn(
                        thread, turn, store, emitter, registry, budget, metric, cancel
                    )
            tool_name = tc["function"]["name"]
            tool_call_id = tc["id"]
            raw_args = tc["function"].get("arguments", "")
            arguments = parse_tool_arguments(raw_args)
            source = "mcp" if mcp_manager.is_mcp_tool(tool_name) else "builtin"

            from agent.tool_access import is_tool_allowed

            if not is_tool_allowed(
                tool_name, allowed_tools, allow_mcp_servers=allow_mcp_servers
            ):
                emitter.tool_pending(
                    thread.id, turn.id, tool_name, arguments, source=source
                )
                emitter.tool_completed(
                    thread.id, turn.id, tool_name, "blocked", source=source
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"Tool {tool_name} is not available in this mode.",
                    }
                )
                continue

            emitter.tool_pending(
                thread.id, turn.id, tool_name, arguments, source=source
            )
            if hooks_runner:
                hooks_runner.run(
                    "on_tool_pending",
                    {"tool_name": tool_name, "arguments": arguments},
                    tool_name=tool_name,
                    thread_id=thread.id,
                    turn_id=turn.id,
                )

            tracking_items = _create_tracking_items(
                tool_name, arguments, config, tool_call_id, raw_args, mcp_manager, thread.id
            )
            for item in tracking_items:
                turn.items.append(item)
                store.append_item(thread, turn.id, item)
                emitter.item_started(thread.id, turn.id, item.type, item.id)

            if hooks_runner:
                pre_hook = hooks_runner.run_event(
                    "on_pre_tool_use",
                    {"tool_name": tool_name, "arguments": arguments},
                    tool_name=tool_name,
                    thread_id=thread.id,
                    turn_id=turn.id,
                )
                if pre_hook.block:
                    _handle_blocked_tool(
                        pre_hook.block_reason or "Blocked by on_pre_tool_use hook",
                        tracking_items,
                        store,
                        thread,
                        turn,
                        emitter,
                        tool_call_id,
                        messages,
                        config,
                    )
                    continue

            policy_block = _exec_policy_block(tool_name, arguments, config)
            if policy_block:
                _handle_blocked_tool(
                    policy_block,
                    tracking_items,
                    store,
                    thread,
                    turn,
                    emitter,
                    tool_call_id,
                    messages,
                    config,
                )
                continue

            requires_approval = tool_requires_approval(tool_name, mcp_manager, config)

            if requires_approval and needs_approval_prompt(
                tool_name,
                arguments,
                config,
                turn_state=turn_state,
                session=session,
            ):
                summary = format_tool_summary(tool_name, arguments)
                diff_preview = _approval_diff_preview_for_tool(
                    tool_name, arguments, config
                )
                emitter.approval_requested(
                    thread.id,
                    turn.id,
                    tool_name,
                    summary,
                    diff_preview=diff_preview,
                )
                approved = _prompt_with_hooks(
                    hooks_runner,
                    thread,
                    turn,
                    tool_name,
                    arguments,
                    summary,
                    config,
                    turn_state,
                    session,
                )
                if not approved:
                    _mark_denied(tracking_items, store, thread, turn.id)
                    for item in tracking_items:
                        emitter.item_completed(
                            thread.id, turn.id, item.type, item.id, "denied"
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": "User denied this action.",
                        }
                    )
                    continue
                _mark_approved(tracking_items)
            elif requires_approval:
                _mark_approved(tracking_items)
                if session:
                    session.approval_cache.record(tool_name, arguments)

            block_reason, retryable = _precheck_tool(
                tool_name,
                arguments,
                config,
                mcp_manager,
                session=session,
                read_only_review=read_only_review,
            )
            if block_reason and retryable and session:
                apply_sandbox_escalation_for_reason(session, block_reason)
                block_reason, _retryable = _precheck_tool(
                    tool_name,
                    arguments,
                    config,
                    mcp_manager,
                    session=session,
                    read_only_review=read_only_review,
                )
            if block_reason:
                _handle_blocked_tool(
                    block_reason,
                    tracking_items,
                    store,
                    thread,
                    turn,
                    emitter,
                    tool_call_id,
                    messages,
                    config,
                )
                continue

            if tool_name == "run_command" and config.execution.backend == "ssh":
                session.ssh_command_approved = True

            cancel.check()

            if tool_name == "spawn_worker":
                if not registry:
                    result_text = "spawn_worker requires multi_agent.enabled or --multi-agent"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_text,
                        }
                    )
                    continue
                elif spawn_count >= config.multi_agent.max_workers_per_turn:
                    result_text = (
                        f"Worker spawn limit reached "
                        f"({config.multi_agent.max_workers_per_turn} per turn)."
                    )
                    for item in tracking_items:
                        if isinstance(item, (CollabSpawnItem, CollabWorkerItem)):
                            item.status = "failed"
                            if hasattr(item, "summary"):
                                item.summary = result_text
                        store.append_item(thread, turn.id, item)
                        emitter.item_completed(
                            thread.id, turn.id, item.type, item.id, "failed"
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_text,
                        }
                    )
                    continue
                spawn_count += 1
                if registry:
                    registry.spawn_count = spawn_count
                if budget:
                    metric = budget.record_worker_spawn(1)
                    if metric and budget.should_kill():
                        return _budget_kill_turn(
                            thread, turn, store, emitter, registry, budget, metric, cancel
                        )
                if registry:
                    collab_item_in = (
                        tracking_items[0]
                        if tracking_items
                        and isinstance(tracking_items[0], CollabWorkerItem)
                        else None
                    )
                    result_text, collab_item = registry.enqueue(
                        thread,
                        arguments,
                        depth=worker_depth,
                        turn_id=turn.id,
                        item=collab_item_in,
                    )
                    if tracking_items and isinstance(
                        tracking_items[0], CollabWorkerItem
                    ):
                        item = tracking_items[0]
                        item.worker_id = collab_item.worker_id
                        item.worker_thread_id = collab_item.worker_thread_id
                        item.status = collab_item.status
                        item.summary = collab_item.summary
                        store.append_item(thread, turn.id, item)
                        emitter.item_completed(
                            thread.id, turn.id, item.type, item.id, item.status
                        )
                    emitter.tool_completed(
                        thread.id,
                        turn.id,
                        tool_name,
                        collab_item.status,
                        source=source,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_text,
                        }
                    )
                    continue

            if tool_name == "wait_workers":
                if not registry:
                    result_text = "No worker registry available."
                else:
                    result_text = registry.wait_workers(
                        arguments.get("worker_ids"),
                        timeout_sec=arguments.get("timeout_sec"),
                        mode=arguments.get("mode", "all"),
                    )
                    _sync_worker_items(thread, turn, registry, store)
                emitter.tool_completed(
                    thread.id, turn.id, tool_name, "completed", source=source
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    }
                )
                continue

            if tool_name == "spawn_worker_batch":
                if not registry:
                    result_text = "spawn_worker_batch requires multi_agent.enabled"
                else:
                    tasks = arguments.get("tasks") or []
                    if spawn_count + len(tasks) > config.multi_agent.max_workers_per_turn:
                        result_text = (
                            f"Worker spawn limit reached "
                            f"({config.multi_agent.max_workers_per_turn} per turn)."
                        )
                    else:
                        spawn_count += len(tasks)
                        registry.spawn_count = spawn_count
                        if budget:
                            metric = budget.record_worker_spawn(len(tasks))
                            if metric and budget.should_kill():
                                return _budget_kill_turn(
                                    thread, turn, store, emitter, registry, budget, metric, cancel
                                )
                        with trace_span("worker.spawn_batch", count=len(tasks)):
                            result_text, _collabs = registry.enqueue_batch(
                                thread,
                                tasks,
                                depth=worker_depth,
                                turn_id=turn.id,
                            )
                emitter.tool_completed(
                    thread.id, turn.id, tool_name, "completed", source=source
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    }
                )
                continue

            if tool_name == "get_worker_graph":
                result_text = (
                    registry.get_worker_graph()
                    if registry
                    else json.dumps({"nodes": [], "edges": [], "status": "idle"})
                )
                emitter.tool_completed(
                    thread.id, turn.id, tool_name, "completed", source=source
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    }
                )
                continue

            if tool_name == "list_workers":
                result_text = (
                    registry.list_workers()
                    if registry
                    else json.dumps({"workers": []})
                )
                emitter.tool_completed(
                    thread.id, turn.id, tool_name, "completed", source=source
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    }
                )
                continue

            if tool_name == "request_user_input":
                result_text = _handle_request_user_input(
                    thread,
                    turn,
                    store,
                    emitter,
                    arguments,
                    config,
                    headless_json=headless_json,
                    session=session,
                    turn_state=turn_state,
                )
                emitter.tool_completed(
                    thread.id, turn.id, tool_name, "completed" if "error" not in result_text else "failed", source=source
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call_id, "content": result_text}
                )
                continue

            if tool_name == "request_permissions":
                result_text = _handle_request_permissions(
                    thread,
                    turn,
                    emitter,
                    arguments,
                    config,
                    session=session,
                    turn_state=turn_state,
                    read_only_review=read_only_review,
                )
                emitter.tool_completed(
                    thread.id,
                    turn.id,
                    tool_name,
                    "completed" if "denied" not in result_text.lower() else "denied",
                    source=source,
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call_id, "content": result_text}
                )
                continue

            if tool_name == "run_command" and not backend_announced:
                backend_announced = True
                img = (
                    config.execution.docker_image_override
                    or config.execution.default_image
                )
                emitter.execution_backend_selected(
                    thread.id,
                    turn.id,
                    backend=config.execution.backend,
                    image=img if config.execution.backend == "docker" else None,
                )
                if config.execution.backend == "ssh":
                    ssh = config.execution.ssh
                    emitter.execution_ssh_connected(
                        thread.id,
                        turn.id,
                        host=ssh.host,
                        user=ssh.user,
                        remote_workspace=ssh.remote_workspace,
                    )

            cancel.check()

            emitter.tool_executing(
                thread.id, turn.id, tool_name, arguments, source=source
            )

            with trace_span("tool.execute", tool=tool_name, backend=config.execution.backend):
                dispatch_result = dispatch_tool(
                    tool_name, arguments, config, mcp_manager=mcp_manager, thread_id=thread.id
                )
            _emit_execution_events(
                dispatch_result, thread.id, turn.id, config, emitter
            )
            result_text = _apply_dispatch_results(
                dispatch_result,
                tracking_items,
                store,
                thread,
                turn.id,
                emitter,
                config=config,
            )
            patch_preview = None
            if tool_name == "apply_patch" and dispatch_result.file_items:
                snippets = [
                    fi.diff_snippet for fi in dispatch_result.file_items if fi.diff_snippet
                ]
                if snippets:
                    patch_preview = "\n".join(snippets)[:500]
            emitter.tool_completed(
                thread.id,
                turn.id,
                tool_name,
                tracking_items[0].status if tracking_items else "completed",
                source=source,
                diff_preview=patch_preview,
                summary=result_text[:200] if tool_name == "apply_patch" else None,
                **_tool_completed_extra(tool_name, tracking_items, result_text),
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text,
                }
            )
            if (
                tool_name == "apply_patch"
                and tracking_items
                and tracking_items[0].status == "completed"
                and config.harness.post_patch_test.strip()
                and not plan_mode
                and not read_only_review
            ):
                hook_output = _run_post_patch_test(config.harness.post_patch_test, config.cwd)
                if hook_output:
                    messages[-1]["content"] = f"{result_text}\n\n[post_patch_test]\n{hook_output}"
            if (
                tool_name == "apply_patch"
                and tracking_items
                and tracking_items[0].status == "completed"
                and config.harness.lsp_diagnostics_after_patch
                and not plan_mode
                and not read_only_review
            ):
                diag = _run_lsp_diagnostics_after_patch(
                    mcp_manager, config, tracking_items[0].path
                )
                if diag:
                    messages[-1]["content"] = f"{messages[-1]['content']}\n\n[lsp_diagnostics]\n{diag}"

    turn.status = "failed"
    fail_item = AgentMessageItem(
        text=f"Stopped: exceeded maximum tool rounds ({config.max_rounds})."
    )
    turn.items.append(fail_item)
    store.append_item(thread, turn.id, fail_item)
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(
        thread.id, turn.id, turn.status, estimated_tokens=estimate_tokens(messages)
    )
    return turn


def _create_tracking_items(
    tool_name: str,
    arguments: dict,
    config: Config,
    tool_call_id: str,
    raw_args: str,
    mcp_manager: McpManager,
    parent_thread_id: str = "",
) -> list[TrackingItem]:
    if mcp_manager.is_mcp_tool(tool_name):
        ref = mcp_manager.tool_map.get(tool_name)
        return [
            McpToolCallItem(
                server=ref.server if ref else "unknown",
                tool=ref.tool if ref else tool_name,
                arguments=arguments,
                status="pending",
                tool_call_id=tool_call_id,
            )
        ]
    if tool_name == "run_command":
        return [
            CommandExecutionItem(
                command=arguments.get("cmd", ""),
                cwd=str(config.cwd),
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
            )
        ]
    if tool_name == "write_file":
        return [
            FileChangeItem(
                path=arguments.get("path", ""),
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
                content=arguments.get("content"),
                change_type="overwrite",
            )
        ]
    if tool_name == "apply_patch":
        return [
            FileChangeItem(
                path="(patch)",
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
                change_type="update",
            )
        ]
    if tool_name == "web_search":
        return [
            WebSearchItem(
                query=arguments.get("query", ""),
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
            )
        ]
    if tool_name == "spawn_worker":
        worker_id = arguments.get("worker_id") or ""
        return [
            CollabWorkerItem(
                worker_id=worker_id or "pending",
                worker_thread_id="",
                parent_thread_id=parent_thread_id,
                task=arguments.get("task", ""),
                status="queued",
                title=arguments.get("title"),
                model=arguments.get("model"),
                execution_backend=arguments.get("execution_backend"),
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
            )
        ]
    return []


def _tool_completed_extra(
    tool_name: str,
    tracking_items: list[TrackingItem],
    result_text: str,
    *,
    max_output: int = 2000,
) -> dict[str, Any]:
    """Build optional output/exit_code/duration fields for tool_completed events."""
    extra: dict[str, Any] = {}
    if tool_name == "apply_patch":
        return extra
    if tracking_items:
        item = tracking_items[0]
        if isinstance(item, CommandExecutionItem):
            extra["output"] = (item.output or result_text or "")[:max_output] or None
            extra["exit_code"] = item.exit_code
            extra["duration_ms"] = item.duration_ms
            return extra
        if isinstance(item, McpToolCallItem):
            extra["output"] = (item.output or item.error or result_text or "")[:max_output] or None
            extra["duration_ms"] = item.duration_ms
            return extra
        if isinstance(item, WebSearchItem):
            extra["output"] = result_text[:max_output] if result_text else None
            return extra
    if result_text and tool_name != "apply_patch":
        extra["output"] = result_text[:max_output]
    return extra


def _mark_denied(items: list[TrackingItem], store, thread, turn_id) -> None:
    for item in items:
        item.status = "denied"
        if isinstance(item, CommandExecutionItem):
            item.output = "User denied this action."
        elif isinstance(item, McpToolCallItem):
            item.output = "User denied this action."
        elif isinstance(item, WebSearchItem):
            item.error = "User denied this action."
        else:
            item.summary = "User denied this action."
        store.append_item(thread, turn_id, item)


def _mark_approved(items: list[TrackingItem]) -> None:
    for item in items:
        item.status = "approved"


def _run_pre_turn_context_guard(
    thread: Thread,
    config: Config,
    store: ThreadStore,
    client,
    *,
    project_rules: str,
    active_skills,
    skills_max_body: int,
    memories_text: str,
    system_prompt_append: str,
    hooks_runner,
) -> list | None:
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    pre_limit = int(snap.effective_window * config.compaction.pre_turn_threshold)
    if snap.estimated_total <= pre_limit:
        return None

    t1 = compact_tool_outputs(thread, config, store, project_rules=project_rules)
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    if snap.estimated_total <= pre_limit:
        return _rebuild_messages(
            thread,
            config,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
        )

    cr = compact_thread_if_needed(
        thread,
        config,
        store,
        client,
        hooks_runner=hooks_runner,
        project_rules=project_rules,
        force=True,
    )
    if cr.performed:
        invalidate_context_cache(thread.id)
        return _rebuild_messages(
            thread,
            config,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
        )
    if t1.performed:
        return _rebuild_messages(
            thread,
            config,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
        )
    return None


def _rebuild_messages(
    thread: Thread,
    config: Config,
    *,
    project_rules: str,
    active_skills,
    skills_max_body: int,
    memories_text: str,
    system_prompt_append: str,
) -> list:
    return build_thread_messages(
        thread,
        active_skills=active_skills,
        skills_max_body=skills_max_body,
        project_rules=project_rules,
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
        memories_text=memories_text,
        system_prompt_append=system_prompt_append,
    )


def _spill_item_output(
    config: Config,
    *,
    thread_id: str,
    item_id: str,
    text: str | None,
) -> tuple[str | None, str | None, int | None]:
    if not text:
        return text, None, None
    from agent.artifacts import spill_if_large

    limit = min(config.max_tool_output, config.context.artifact_inline_limit)
    inline, path = spill_if_large(
        text, thread_id=thread_id, item_id=item_id, inline_limit=limit
    )
    return inline, path, len(text)


def _apply_dispatch_results(
    result: DispatchResult,
    tracking_items: list[TrackingItem],
    store: ThreadStore,
    thread: Thread,
    turn_id: str,
    emitter: EventEmitter,
    *,
    config: Config | None = None,
) -> str:
    if result.mcp_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, McpToolCallItem):
            item.status = result.mcp_item.status
            raw_out = result.mcp_item.output
            if config and raw_out:
                inline, path, chars = _spill_item_output(
                    config, thread_id=thread.id, item_id=item.id, text=raw_out
                )
                item.output = inline
                item.artifact_path = path
                item.output_chars = chars
            else:
                item.output = raw_out
            item.error = result.mcp_item.error
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.command_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, CommandExecutionItem):
            item.status = result.command_item.status
            raw_out = result.command_item.output
            if config and raw_out:
                inline, path, chars = _spill_item_output(
                    config, thread_id=thread.id, item_id=item.id, text=raw_out
                )
                item.output = inline
                item.artifact_path = path
                item.output_chars = chars
            else:
                item.output = raw_out
            item.exit_code = result.command_item.exit_code
            item.duration_ms = result.command_item.duration_ms
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.web_search_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, WebSearchItem):
            item.status = result.web_search_item.status
            item.results = result.web_search_item.results
            item.error = result.web_search_item.error
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.file_items:
        primary = tracking_items[0] if tracking_items else None
        if isinstance(primary, FileChangeItem) and result.file_items:
            first = result.file_items[0]
            primary.path = first.path if len(result.file_items) == 1 else "(patch)"
            primary.status = first.status
            primary.summary = result.text
            primary.change_type = first.change_type
            primary.diff_snippet = first.diff_snippet
            store.append_item(thread, turn_id, primary)
            emitter.item_completed(
                thread.id, turn_id, primary.type, primary.id, primary.status
            )
            for extra in result.file_items[1:]:
                turn = thread.turns[-1]
                turn.items.append(extra)
                store.append_item(thread, turn_id, extra)
                emitter.item_completed(
                    thread.id, turn_id, extra.type, extra.id, extra.status
                )
        return result.text

    return result.text


def _finalize_cancelled(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    *,
    config: Config | None = None,
) -> None:
    turn.status = "cancelled"
    msg = AgentMessageItem(text="Turn cancelled by user.")
    turn.items.append(msg)
    store.append_item(thread, turn.id, msg)
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    if config is not None:
        from agent.notify import fire_notify

        fire_notify(
            config.notify,
            thread_id=thread.id,
            turn_id=turn.id,
            status=turn.status,
            cwd=config.cwd,
        )


def _run_parallel_read_tool_round(
    tool_calls_list: list,
    *,
    thread: Thread,
    turn: Turn,
    config: Config,
    store: ThreadStore,
    mcp_manager: McpManager,
    messages: list,
    emitter: EventEmitter,
    cancel: CancelToken,
    allowed_tools: list[str] | None,
    allow_mcp_servers: list[str] | None = None,
    turn_state: TurnApprovalState,
    session: HarnessSession,
    hooks_runner,
    max_workers: int,
) -> None:
    prepared: list[dict] = []

    for tc in tool_calls_list:
        cancel.check()
        tool_name = tc["function"]["name"]
        tool_call_id = tc["id"]
        raw_args = tc["function"].get("arguments", "")
        arguments = parse_tool_arguments(raw_args)
        source = "mcp" if mcp_manager.is_mcp_tool(tool_name) else "builtin"

        from agent.tool_access import is_tool_allowed

        if not is_tool_allowed(
            tool_name, allowed_tools, allow_mcp_servers=allow_mcp_servers
        ):
            emitter.tool_pending(thread.id, turn.id, tool_name, arguments, source=source)
            emitter.tool_completed(thread.id, turn.id, tool_name, "blocked", source=source)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"Tool {tool_name} is not available in this mode.",
                }
            )
            continue

        emitter.tool_pending(thread.id, turn.id, tool_name, arguments, source=source)
        if hooks_runner:
            hooks_runner.run(
                "on_tool_pending",
                {"tool_name": tool_name, "arguments": arguments},
                tool_name=tool_name,
                thread_id=thread.id,
                turn_id=turn.id,
            )

        tracking_items = _create_tracking_items(
            tool_name, arguments, config, tool_call_id, raw_args, mcp_manager, thread.id
        )
        for item in tracking_items:
            turn.items.append(item)
            store.append_item(thread, turn.id, item)
            emitter.item_started(thread.id, turn.id, item.type, item.id)

        if hooks_runner:
            pre_hook = hooks_runner.run_event(
                "on_pre_tool_use",
                {"tool_name": tool_name, "arguments": arguments},
                tool_name=tool_name,
                thread_id=thread.id,
                turn_id=turn.id,
            )
            if pre_hook.block:
                _handle_blocked_tool(
                    pre_hook.block_reason or "Blocked by on_pre_tool_use hook",
                    tracking_items,
                    store,
                    thread,
                    turn,
                    emitter,
                    tool_call_id,
                    messages,
                    config,
                )
                continue

        policy_block = _exec_policy_block(tool_name, arguments, config)
        if policy_block:
            _handle_blocked_tool(
                policy_block,
                tracking_items,
                store,
                thread,
                turn,
                emitter,
                tool_call_id,
                messages,
                config,
            )
            continue

        requires_approval = tool_requires_approval(tool_name, mcp_manager, config)
        if requires_approval and needs_approval_prompt(
            tool_name,
            arguments,
            config,
            turn_state=turn_state,
            session=session,
        ):
            summary = format_tool_summary(tool_name, arguments)
            diff_preview = _approval_diff_preview_for_tool(
                tool_name, arguments, config
            )
            emitter.approval_requested(
                thread.id,
                turn.id,
                tool_name,
                summary,
                diff_preview=diff_preview,
            )
            approved = _prompt_with_hooks(
                hooks_runner,
                thread,
                turn,
                tool_name,
                arguments,
                summary,
                config,
                turn_state,
                session,
            )
            if not approved:
                _mark_denied(tracking_items, store, thread, turn.id)
                for item in tracking_items:
                    emitter.item_completed(thread.id, turn.id, item.type, item.id, "denied")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "User denied this action.",
                    }
                )
                continue
            _mark_approved(tracking_items)
        elif requires_approval:
            _mark_approved(tracking_items)
            session.approval_cache.record(tool_name, arguments)

        block_reason, retryable = _precheck_tool(
            tool_name,
            arguments,
            config,
            mcp_manager,
            session=session,
        )
        if block_reason and retryable:
            apply_sandbox_escalation_for_reason(session, block_reason)
            block_reason, _ = _precheck_tool(
                tool_name,
                arguments,
                config,
                mcp_manager,
                session=session,
            )
        if block_reason:
            _handle_blocked_tool(
                block_reason,
                tracking_items,
                store,
                thread,
                turn,
                emitter,
                tool_call_id,
                messages,
                config,
            )
            continue

        prepared.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "tracking_items": tracking_items,
                "source": source,
            }
        )

    if not prepared:
        return

    def _dispatch_entry(entry: dict) -> str:
        emitter.tool_executing(
            thread.id,
            turn.id,
            entry["tool_name"],
            entry["arguments"],
            source=entry["source"],
        )
        dispatch_result = dispatch_tool(
            entry["tool_name"],
            entry["arguments"],
            config,
            mcp_manager=mcp_manager,
            thread_id=thread.id,
        )
        _emit_execution_events(dispatch_result, thread.id, turn.id, config, emitter)
        return _apply_dispatch_results(
            dispatch_result,
            entry["tracking_items"],
            store,
            thread,
            turn.id,
            emitter,
            config=config,
        )

    jobs = [(index, lambda entry=entry: _dispatch_entry(entry)) for index, entry in enumerate(prepared)]
    results = run_parallel_tool_dispatches(jobs, max_workers=max_workers)

    for (index, result_text), entry in zip(results, prepared, strict=True):
        tool_name = entry["tool_name"]
        tracking_items = entry["tracking_items"]
        emitter.tool_completed(
            thread.id,
            turn.id,
            tool_name,
            tracking_items[0].status if tracking_items else "completed",
            source=entry["source"],
            **_tool_completed_extra(tool_name, tracking_items, result_text),
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": entry["tool_call_id"],
                "content": result_text,
            }
        )


def _prompt_with_hooks(
    hooks_runner,
    thread: Thread,
    turn: Turn,
    tool_name: str,
    arguments: dict,
    summary: str,
    config: Config,
    turn_state: TurnApprovalState,
    session: HarnessSession,
) -> bool:
    if hooks_runner:
        hook = hooks_runner.run_event(
            "on_permission_request",
            {"tool_name": tool_name, "arguments": arguments, "summary": summary},
            tool_name=tool_name,
            thread_id=thread.id,
            turn_id=turn.id,
        )
        if hook.context_append:
            summary = f"{summary}\n{hook.context_append}"
    return prompt_approval(
        tool_name,
        arguments,
        auto_approve=config.auto_approve,
        turn_state=turn_state,
        session=session,
        config=config,
    )


def _exec_policy_block(tool_name: str, arguments: dict, config: Config) -> str | None:
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        return exec_policy_block_reason(cmd, config)
    return None


def _precheck_tool(
    tool_name: str,
    arguments: dict,
    config: Config,
    mcp_manager: McpManager,
    *,
    session: HarnessSession | None = None,
    read_only_review: bool = False,
) -> tuple[str | None, bool]:
    if read_only_review and tool_name in ("apply_patch", "write_file", "git_commit"):
        return f"{tool_name} blocked in read-only review mode (pass --fix to allow writes)", False
    if tool_name == "request_permissions" and read_only_review:
        return "permission escalation denied in read-only review mode", False
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        decision = check_run_command(cmd, config.cwd, config.sandbox_mode, session=session)
        if decision.blocked:
            return decision.reason, decision.retryable
    elif tool_name == "write_file":
        decision = check_write_file(
            arguments.get("path", ""),
            config.cwd,
            config.sandbox_mode,
            memories=config.memories,
        )
        if decision.blocked:
            return decision.reason, decision.retryable
    elif tool_name == "apply_patch":
        decision = check_apply_patch(config.cwd, config.sandbox_mode)
        if decision.blocked:
            return decision.reason, decision.retryable
    elif tool_name == "web_search":
        if config.sandbox_mode.value == "read-only":
            return "web search denied in read-only sandbox", False
    elif tool_name in ("spawn_worker", "spawn_worker_batch"):
        if not config.multi_agent.enabled:
            return f"{tool_name} requires multi_agent.enabled or --multi-agent", False
    elif tool_name in ("wait_workers", "list_workers", "get_worker_graph"):
        if not config.multi_agent.enabled:
            return f"{tool_name} requires multi_agent.enabled or --multi-agent", False
    elif mcp_manager.is_mcp_tool(tool_name):
        ref = mcp_manager.tool_map.get(tool_name)
        decision = check_mcp_tool(
            tool_name,
            config.sandbox_mode,
            require_approval=ref.require_approval if ref else True,
        )
        if decision.blocked:
            return decision.reason, decision.retryable
    return None, False


def _handle_blocked_tool(
    reason: str,
    tracking_items: list[TrackingItem],
    store: ThreadStore,
    thread: Thread,
    turn: Turn,
    emitter: EventEmitter,
    tool_call_id: str,
    messages: list,
    config: Config,
) -> None:
    cmd = None
    for item in tracking_items:
        item.status = "denied"
        if isinstance(item, CommandExecutionItem):
            item.output = reason
            cmd = item.command
        elif isinstance(item, McpToolCallItem):
            item.output = reason
        elif isinstance(item, WebSearchItem):
            item.error = reason
        else:
            item.summary = reason
        store.append_item(thread, turn.id, item)
        emitter.item_completed(thread.id, turn.id, item.type, item.id, "denied")
    emitter.sandbox_blocked(
        thread.id,
        turn.id,
        mode=config.sandbox_mode.value,
        reason=reason,
        command=cmd,
    )
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"Blocked by sandbox/policy: {reason}",
        }
    )


def _emit_execution_events(
    result: DispatchResult,
    thread_id: str,
    turn_id: str,
    config: Config,
    emitter: EventEmitter,
) -> None:
    meta = result.execution_meta or result.isolation_meta
    if not meta:
        return
    if meta.get("isolated"):
        emitter.isolation_applied(
            thread_id,
            turn_id,
            pid=meta.get("pid"),
            cwd=meta.get("cwd", str(config.cwd)),
            stripped_env_count=meta.get("stripped_env_count", 0),
        )
    if meta.get("backend") == "docker":
        image = meta.get("image") or config.execution.default_image
        emitter.execution_docker_started(
            thread_id,
            turn_id,
            image=image,
            container_id=meta.get("container_id"),
        )
        if result.command_item:
            emitter.execution_docker_completed(
                thread_id,
                turn_id,
                image=image,
                exit_code=result.command_item.exit_code or -1,
            )
    if meta.get("backend") == "ssh":
        host = meta.get("remote_host") or config.execution.ssh.host
        emitter.execution_ssh_completed(
            thread_id,
            turn_id,
            host=host,
            exit_code=result.command_item.exit_code if result.command_item else meta.get("exit_code", -1),
            duration_ms=result.command_item.duration_ms if result.command_item else 0,
        )
    if result.file_tool_meta and result.file_tool_meta.get("via_mount"):
        path = ""
        if result.file_items:
            path = result.file_items[0].path
        emitter.execution_docker_file_tool_applied(
            thread_id,
            turn_id,
            tool_name=result.file_tool_meta.get("tool_name", "file"),
            path=path,
            image=result.file_tool_meta.get("image"),
        )


def _sync_worker_items(
    thread: Thread,
    turn: Turn,
    registry: WorkerRegistry,
    store: ThreadStore,
) -> None:
    for item in turn.items:
        if not isinstance(item, CollabWorkerItem):
            continue
        record = registry._workers.get(item.worker_id)
        if not record:
            continue
        item.worker_thread_id = record.worker_thread_id
        item.status = record.status  # type: ignore[assignment]
        item.summary = record.summary
        store.append_item(thread, turn.id, item)


def brief_args(tool_name: str, arguments: dict) -> str:
    if tool_name == "run_command":
        return arguments.get("cmd", "")
    if tool_name == "write_file":
        return arguments.get("path", "")
    if tool_name == "apply_patch":
        from tools.patch import format_patch_brief

        return format_patch_brief(arguments.get("patch", ""))
    if tool_name == "read_file":
        return arguments.get("path", "")
    if tool_name == "search_repo":
        return arguments.get("pattern", "")
    if tool_name == "web_search":
        return arguments.get("query", "")
    if tool_name == "spawn_worker":
        return arguments.get("task", "")[:80]
    if tool_name.startswith("mcp__"):
        return str(arguments)[:80]
    return str(arguments)


def _run_lsp_diagnostics_after_patch(
    mcp_manager: McpManager,
    config: Config,
    path: str,
) -> str:
    tool_name = "mcp__lsp__lsp_diagnostics"
    if not mcp_manager.is_mcp_tool(tool_name):
        return ""
    output, exit_code, _err = mcp_manager.call_tool(
        tool_name,
        {"path": path},
        max_output=config.max_tool_output,
    )
    if exit_code != 0:
        return ""
    return output.strip()[:4000]


def _run_post_patch_test(command: str, cwd: Path) -> str:
    import subprocess

    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return output.strip()[:4000] or f"(exit {proc.returncode}, no output)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"post_patch_test failed: {exc}"


def _cost_cap_kill_turn(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    limit: float,
    observed: float,
) -> Turn:
    msg = AgentMessageItem(
        text=(
            f"Turn stopped: estimated cost ${observed:.4f} exceeded "
            f"max_cost_usd_per_turn=${limit:.4f}."
        )
    )
    turn.items.append(msg)
    store.append_item(thread, turn.id, msg)
    turn.status = "failed"
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.error(thread.id, msg.text)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    return turn


def _budget_kill_turn(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    registry: WorkerRegistry | None,
    budget,
    metric: str,
    cancel: CancelToken,
) -> Turn:
    from agent.harness.active_turns import ActiveTurnRegistry

    snap = budget.snapshot
    emitter.multi_agent_budget_exceeded(
        thread.id,
        turn.id,
        metric=metric,
        limit=snap.last_limit,
        observed=snap.last_observed,
    )
    from agent.multi_agent.budget_state import save_budget_state

    save_budget_state(thread.id, budget.to_dict())
    if registry:
        with registry._lock:
            registry._pending_queue.clear()
            for rec in registry._workers.values():
                if rec.status in ("queued", "blocked", "running"):
                    rec.status = "cancelled"
                    rec._done.set()
        registry._dag_status = "cancelled"
    cancel.cancel()
    ActiveTurnRegistry.global_registry().cancel(thread.id)
    msg = (
        f"Supervisor stopped: budget exceeded ({metric}). "
        f"Snapshot: {budget.to_dict()}"
    )
    agent_item = AgentMessageItem(text=msg)
    turn.items.append(agent_item)
    store.append_item(thread, turn.id, agent_item)
    turn.status = "failed"
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    return turn


def _handle_request_user_input(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    arguments: dict,
    config: Config,
    *,
    headless_json: bool,
    session: HarnessSession,
    turn_state: TurnApprovalState,
) -> str:
    from agent.user_input import normalize_user_input_questions, resolve_user_input

    specs = normalize_user_input_questions(arguments)
    if not specs:
        return json.dumps({"error": "question or questions required"})

    if not config.auto_approve and needs_approval_prompt(
        "request_user_input", arguments, config, turn_state=turn_state, session=session
    ):
        summary = format_tool_summary("request_user_input", arguments)
        emitter.approval_requested(thread.id, turn.id, "request_user_input", summary)
        if not prompt_approval(
            "request_user_input",
            arguments,
            auto_approve=config.auto_approve,
            turn_state=turn_state,
            session=session,
        ):
            return json.dumps({"error": "user denied input prompt"})

    total = len(specs)
    results: list[dict[str, Any]] = []

    for index, spec in enumerate(specs, start=1):
        question = spec["question"]
        options = spec.get("options")
        allow_free = bool(spec.get("allow_free_text", True))

        emitter.user_input_requested(
            thread.id,
            turn.id,
            question=question,
            options=options,
            allow_free_text=allow_free,
            question_index=index,
            question_total=total,
        )
        answer, selected, err = resolve_user_input(
            question,
            options,
            allow_free_text=allow_free,
            auto_approve=config.auto_approve,
            headless_json=headless_json,
            question_key=question,
            question_index=index,
            question_total=total,
        )
        if err:
            emitter.error(thread.id, err)
            return json.dumps({"error": err, "partial_answers": results})

        item = UserInputItem(
            question=question,
            answer=answer or "",
            selected_option=selected,
            options=options or [],
        )
        turn.items.append(item)
        store.append_item(thread, turn.id, item)
        emitter.user_input(
            thread.id,
            turn.id,
            question=question,
            answer=answer or "",
            selected_option=selected,
        )
        entry = {"question": question, "answer": answer, "selected_option": selected}
        results.append(entry)

    first = results[0]
    payload: dict[str, Any] = {
        "answer": first.get("answer"),
        "selected_option": first.get("selected_option"),
        "answers": results,
    }
    return json.dumps(payload)


def _handle_request_permissions(
    thread: Thread,
    turn: Turn,
    emitter: EventEmitter,
    arguments: dict,
    config: Config,
    *,
    session: HarnessSession,
    turn_state: TurnApprovalState,
    read_only_review: bool,
) -> str:
    if read_only_review:
        emitter.permission_denied(
            thread.id, turn.id, scope=arguments.get("scope", ""), reason="read-only review mode"
        )
        return json.dumps({"granted": False, "reason": "read-only review mode"})

    scope = arguments.get("scope", "")
    reason = arguments.get("reason", "")
    duration = arguments.get("duration", "turn")
    if scope not in ("network", "write_outside_cwd", "full_access"):
        return json.dumps({"granted": False, "reason": f"invalid scope: {scope}"})

    summary = format_tool_summary("request_permissions", arguments)
    if not config.auto_approve and needs_approval_prompt(
        "request_permissions", arguments, config, turn_state=turn_state, session=session
    ):
        emitter.approval_requested(thread.id, turn.id, "request_permissions", summary)
        if not prompt_approval(
            "request_permissions",
            arguments,
            auto_approve=config.auto_approve,
            turn_state=turn_state,
            session=session,
        ):
            emitter.permission_denied(thread.id, turn.id, scope=scope, reason="user denied")
            return json.dumps({"granted": False, "reason": "user denied"})

    session.grant_permission(scope, duration=duration)
    emitter.permission_escalated(
        thread.id, turn.id, scope=scope, duration=duration, reason=reason
    )
    return json.dumps({"granted": True, "scope": scope, "duration": duration})
