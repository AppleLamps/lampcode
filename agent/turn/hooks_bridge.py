"""Hooks integration for tool approvals."""
from __future__ import annotations

from agent.config import Config
from agent.models import Thread, Turn
from agent.session import HarnessSession
from approval.gate import TurnApprovalState, prompt_approval

def prompt_with_hooks(
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
