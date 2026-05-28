"""Turn state machine: model rounds, tool calls, compaction."""
from __future__ import annotations

import json
from typing import Any, Callable

from agent.cancel import CancelToken, CancelledError
import agent.loop as loop_shim
from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens
from agent.context_meter import extract_api_context_tokens, invalidate_context_cache
from agent.events import EventEmitter
from agent.execution.factory import backend_display
from agent.mcp.manager import McpManager
from agent.models import (
    AgentMessageItem,
    CollabSpawnItem,
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    PlanProposalItem,
    Thread,
    Turn,
    UserMessageItem,
    WebSearchItem,
)
from agent.multi_agent.registry import WorkerRegistry
from agent.sandbox.retry import apply_sandbox_escalation_for_reason
from agent.session import HarnessSession
from agent.tool_round import can_parallelize_tool_round
from agent.turn.budget import budget_kill_turn, cost_cap_kill_turn
from agent.turn.context_guard import run_pre_turn_context_guard
from agent.turn.helpers import approval_diff_preview_for_tool
from agent.turn.hooks_bridge import prompt_with_hooks
from agent.turn.special_tools import handle_request_permissions, handle_request_user_input
from agent.turn.tools.dispatch import (
    apply_dispatch_results,
    emit_execution_events,
    exec_policy_block,
    handle_blocked_tool,
    precheck_tool,
    sync_worker_items,
)
from agent.turn.tools.parallel import run_parallel_read_tool_round
from agent.turn.tools.post_patch import run_lsp_diagnostics_after_patch
from agent.turn.tools.tracking import (
    create_tracking_items,
    mark_approved,
    mark_denied,
    tool_completed_extra,
)
from agent.store import ThreadStore
from agent.telemetry import trace_span
from approval.gate import TurnApprovalState, format_tool_summary, needs_approval_prompt
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.registry import DispatchResult, parse_tool_arguments, tool_requires_approval

def run_loop(
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
                return budget_kill_turn(
                    thread, turn, store, emitter, registry, budget, metric, cancel
                )

        compact_result = loop_shim.compact_thread_if_needed(
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

        guard_messages = run_pre_turn_context_guard(
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
                compact_result = loop_shim.compact_thread_if_needed(
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
            return cost_cap_kill_turn(
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
                return budget_kill_turn(
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
            run_parallel_read_tool_round(
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
                    return budget_kill_turn(
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

            tracking_items = create_tracking_items(
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
                    handle_blocked_tool(
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

            policy_block = exec_policy_block(tool_name, arguments, config)
            if policy_block:
                handle_blocked_tool(
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
                diff_preview = approval_diff_preview_for_tool(
                    tool_name, arguments, config
                )
                emitter.approval_requested(
                    thread.id,
                    turn.id,
                    tool_name,
                    summary,
                    diff_preview=diff_preview,
                )
                approved = prompt_with_hooks(
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
                    mark_denied(tracking_items, store, thread, turn.id)
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
                mark_approved(tracking_items)
            elif requires_approval:
                mark_approved(tracking_items)
                if session:
                    session.approval_cache.record(tool_name, arguments)

            block_reason, retryable = precheck_tool(
                tool_name,
                arguments,
                config,
                mcp_manager,
                session=session,
                read_only_review=read_only_review,
            )
            if block_reason and retryable and session:
                apply_sandbox_escalation_for_reason(session, block_reason)
                block_reason, _retryable = precheck_tool(
                    tool_name,
                    arguments,
                    config,
                    mcp_manager,
                    session=session,
                    read_only_review=read_only_review,
                )
            if block_reason:
                handle_blocked_tool(
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
                        return budget_kill_turn(
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
                    sync_worker_items(thread, turn, registry, store)
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
                                return budget_kill_turn(
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
                result_text = handle_request_user_input(
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
                result_text = handle_request_permissions(
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
                dispatch_result = loop_shim.dispatch_tool(
                    tool_name, arguments, config, mcp_manager=mcp_manager, thread_id=thread.id
                )
            emit_execution_events(
                dispatch_result, thread.id, turn.id, config, emitter
            )
            result_text = apply_dispatch_results(
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
                **tool_completed_extra(tool_name, tracking_items, result_text),
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
                hook_output = loop_shim._run_post_patch_test(
                    config.harness.post_patch_test, config.cwd
                )
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
                diag = run_lsp_diagnostics_after_patch(
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
