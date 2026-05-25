from __future__ import annotations

import json
from typing import Callable

from agent.cancel import CancelToken, CancelledError
from agent.compaction import compact_thread_if_needed
from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens, load_project_rules
from agent.events import EventEmitter
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
from agent.store import ThreadStore
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
    thread.turns.append(turn)
    emitter.turn_started(thread.id, turn.id)
    MetricsCollector.global_collector().inc("turns_started")
    MetricsCollector.global_collector().adjust_gauge("active_turns", 1)
    ActiveTurnRegistry.global_registry().register(thread.id, turn.id, cancel)

    user_item = UserMessageItem(text=user_text)
    turn.items.append(user_item)
    store.append_item(thread, turn.id, user_item)

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

    client = OpenRouterClient(config)
    tools = get_tool_schemas(
        mcp_manager, config, allow_spawn=config.multi_agent.enabled
    )
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
        return completed_turn
    except CancelledError:
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
        _finalize_cancelled(thread, turn, store, emitter)
        raise
    finally:
        ActiveTurnRegistry.global_registry().unregister(thread.id)
        MetricsCollector.global_collector().adjust_gauge("active_turns", -1)
        try:
            mcp_manager.disconnect_all()
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
) -> Turn:
    spawn_count = resume_spawn_count
    if resume_messages:
        messages = list(resume_messages)
    backend_announced = False
    registry = worker_registry
    if registry:
        registry.spawn_count = spawn_count
    budget = budget_tracker
    for _round in range(config.max_rounds):
        cancel.check()
        if budget:
            metric = budget.tick_wall_clock()
            if metric and budget.should_kill():
                return _budget_kill_turn(
                    thread, turn, store, emitter, registry, budget, metric, cancel
                )

        compact_result = compact_thread_if_needed(thread, config, store, client)
        if compact_result.performed:
            emitter.compaction(thread.id, compact_result.removed_items)
            emitter.compaction_completed(
                thread.id,
                removed_items=compact_result.removed_items,
                summary_chars=compact_result.summary_chars,
                estimated_tokens_before=compact_result.estimated_tokens_before,
                estimated_tokens_after=compact_result.estimated_tokens_after,
            )
            messages = build_thread_messages(
                thread,
                active_skills=active_skills,
                skills_max_body=skills_max_body,
                project_rules=project_rules,
                execution_backend=config.execution.backend,
                sync_enabled=config.execution.ssh.sync_enabled,
            )

        def delta_handler(text: str) -> None:
            cancel.check()
            emitter.agent_delta(thread.id, turn.id, text)
            if on_text_delta:
                on_text_delta(text)

        try:
            result = client.stream_completion(
                messages,
                tools=tools,
                on_delta=delta_handler,
                cancel_token=cancel,
            )
        except CancelledError:
            raise
        except OpenRouterError as exc:
            turn.status = "failed"
            emitter.error(thread.id, str(exc))
            store.append_turn(thread, turn)
            raise

        if result.usage:
            turn.usage.input_tokens = result.usage.get("prompt_tokens")
            turn.usage.output_tokens = result.usage.get("completion_tokens")
        if budget:
            metric = budget.record_usage(result.usage)
            if metric and budget.should_kill():
                return _budget_kill_turn(
                    thread, turn, store, emitter, registry, budget, metric, cancel
                )

        if not result.tool_calls:
            agent_item = AgentMessageItem(text=result.content)
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

        assistant_msg = {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": result.tool_calls,
        }
        messages.append(assistant_msg)

        for tc in result.tool_calls:
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

            emitter.tool_pending(
                thread.id, turn.id, tool_name, arguments, source=source
            )

            tracking_items = _create_tracking_items(
                tool_name, arguments, config, tool_call_id, raw_args, mcp_manager, thread.id
            )
            for item in tracking_items:
                turn.items.append(item)
                store.append_item(thread, turn.id, item)
                emitter.item_started(thread.id, turn.id, item.type, item.id)

            block_reason = _precheck_tool(
                tool_name, arguments, config, mcp_manager
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

            requires_approval = tool_requires_approval(tool_name, mcp_manager, config)

            if requires_approval and needs_approval_prompt(
                tool_name,
                arguments,
                config,
                turn_state=turn_state,
                session=session,
            ):
                summary = format_tool_summary(tool_name, arguments)
                emitter.approval_requested(thread.id, turn.id, tool_name, summary)
                approved = prompt_approval(
                    tool_name,
                    arguments,
                    auto_approve=config.auto_approve,
                    turn_state=turn_state,
                    session=session,
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

            with trace_span("tool.execute", tool=tool_name, backend=config.execution.backend):
                dispatch_result = dispatch_tool(
                    tool_name, arguments, config, mcp_manager=mcp_manager
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
            )
            emitter.tool_completed(
                thread.id,
                turn.id,
                tool_name,
                tracking_items[0].status if tracking_items else "completed",
                source=source,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text,
                }
            )

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


def _apply_dispatch_results(
    result: DispatchResult,
    tracking_items: list[TrackingItem],
    store: ThreadStore,
    thread: Thread,
    turn_id: str,
    emitter: EventEmitter,
) -> str:
    if result.mcp_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, McpToolCallItem):
            item.status = result.mcp_item.status
            item.output = result.mcp_item.output
            item.error = result.mcp_item.error
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.command_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, CommandExecutionItem):
            item.status = result.command_item.status
            item.output = result.command_item.output
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
) -> None:
    turn.status = "cancelled"
    msg = AgentMessageItem(text="Turn cancelled by user.")
    turn.items.append(msg)
    store.append_item(thread, turn.id, msg)
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)


def _precheck_tool(
    tool_name: str,
    arguments: dict,
    config: Config,
    mcp_manager: McpManager,
) -> str | None:
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        policy_reason = exec_policy_block_reason(cmd, config)
        if policy_reason:
            return policy_reason
        decision = check_run_command(cmd, config.cwd, config.sandbox_mode)
        if decision.blocked:
            return decision.reason
    elif tool_name == "write_file":
        decision = check_write_file(
            arguments.get("path", ""), config.cwd, config.sandbox_mode
        )
        if decision.blocked:
            return decision.reason
    elif tool_name == "apply_patch":
        decision = check_apply_patch(config.cwd, config.sandbox_mode)
        if decision.blocked:
            return decision.reason
    elif tool_name == "web_search":
        if config.sandbox_mode.value == "read-only":
            return "web search denied in read-only sandbox"
    elif tool_name in ("spawn_worker", "spawn_worker_batch"):
        if not config.multi_agent.enabled:
            return f"{tool_name} requires multi_agent.enabled or --multi-agent"
    elif tool_name in ("wait_workers", "list_workers", "get_worker_graph"):
        if not config.multi_agent.enabled:
            return f"{tool_name} requires multi_agent.enabled or --multi-agent"
    elif mcp_manager.is_mcp_tool(tool_name):
        ref = mcp_manager.tool_map.get(tool_name)
        decision = check_mcp_tool(
            tool_name,
            config.sandbox_mode,
            require_approval=ref.require_approval if ref else True,
        )
        if decision.blocked:
            return decision.reason
    return None


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
        patch = arguments.get("patch", "")
        return patch.splitlines()[0][:80] if patch else ""
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
