from __future__ import annotations

from typing import Callable

from agent.cancel import CancelToken, CancelledError
from agent.compaction import compact_thread_if_needed
from agent.config import Config
from agent.context import build_thread_messages, load_project_rules
from agent.events import EventEmitter
from agent.mcp.manager import McpManager
from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    SkillActivationItem,
    Thread,
    Turn,
    UserMessageItem,
)
from agent.session import HarnessSession
from agent.settings import load_mcp_config, load_skills_config
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.store import ThreadStore
from approval.gate import TurnApprovalState, format_tool_summary, prompt_approval
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.registry import (
    DispatchResult,
    dispatch_tool,
    get_tool_schemas,
    parse_tool_arguments,
    tool_requires_approval,
)

TrackingItem = CommandExecutionItem | FileChangeItem | McpToolCallItem


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
) -> Turn:
    emitter = events or EventEmitter()
    cancel = cancel_token or CancelToken()
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
    tools = get_tool_schemas(mcp_manager)
    messages = build_thread_messages(
        thread,
        active_skills=active_skills,
        skills_max_body=skills_cfg.max_body_chars,
        project_rules=rules_text,
    )

    try:
        return _run_loop(
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
        )
    except CancelledError:
        _finalize_cancelled(thread, turn, store, emitter)
        raise
    finally:
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
) -> Turn:
    for _round in range(config.max_rounds):
        cancel.check()

        summarized = compact_thread_if_needed(thread, config, store, client)
        if summarized:
            emitter.compaction(thread.id, summarized)
            messages = build_thread_messages(
                thread,
                active_skills=active_skills,
                skills_max_body=skills_max_body,
                project_rules=project_rules,
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

        if not result.tool_calls:
            agent_item = AgentMessageItem(text=result.content)
            turn.items.append(agent_item)
            store.append_item(thread, turn.id, agent_item)
            turn.status = "completed"
            store.append_turn(thread, turn)
            store.save_thread(thread)
            emitter.turn_completed(thread.id, turn.id, turn.status)
            return turn

        assistant_msg = {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": result.tool_calls,
        }
        messages.append(assistant_msg)

        for tc in result.tool_calls:
            cancel.check()
            tool_name = tc["function"]["name"]
            tool_call_id = tc["id"]
            raw_args = tc["function"].get("arguments", "")
            arguments = parse_tool_arguments(raw_args)
            source = "mcp" if mcp_manager.is_mcp_tool(tool_name) else "builtin"

            emitter.tool_pending(
                thread.id, turn.id, tool_name, arguments, source=source
            )

            tracking_items = _create_tracking_items(
                tool_name, arguments, config, tool_call_id, raw_args, mcp_manager
            )
            for item in tracking_items:
                turn.items.append(item)
                store.append_item(thread, turn.id, item)
                emitter.item_started(thread.id, turn.id, item.type, item.id)

            requires_approval = tool_requires_approval(tool_name, mcp_manager)

            if requires_approval:
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

            cancel.check()
            dispatch_result = dispatch_tool(
                tool_name, arguments, config, mcp_manager=mcp_manager
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
    emitter.turn_completed(thread.id, turn.id, turn.status)
    return turn


def _create_tracking_items(
    tool_name: str,
    arguments: dict,
    config: Config,
    tool_call_id: str,
    raw_args: str,
    mcp_manager: McpManager,
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
    return []


def _mark_denied(items: list[TrackingItem], store, thread, turn_id) -> None:
    for item in items:
        item.status = "denied"
        if isinstance(item, CommandExecutionItem):
            item.output = "User denied this action."
        elif isinstance(item, McpToolCallItem):
            item.output = "User denied this action."
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
    if tool_name.startswith("mcp__"):
        return str(arguments)[:80]
    return str(arguments)
