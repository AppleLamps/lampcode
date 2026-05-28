"""Turn execution entrypoint (facade). Implementation lives in agent.turn."""

from __future__ import annotations

from agent.compaction import compact_thread_if_needed
from agent.turn.budget import budget_kill_turn as _budget_kill_turn
from agent.turn.finalize import finalize_cancelled as _finalize_cancelled
from agent.turn.helpers import approval_diff_preview_for_tool as _approval_diff_preview_for_tool
from agent.turn.helpers import brief_args
from agent.turn.special_tools import (
    handle_request_permissions as _handle_request_permissions,
)
from agent.turn.special_tools import handle_request_user_input as _handle_request_user_input
from agent.turn.state_machine import run_loop as _run_loop
from agent.turn.tools.dispatch import precheck_tool as _precheck_tool
from agent.turn.tools.post_patch import run_post_patch_test as _run_post_patch_test
from agent.turn.types import TrackingItem
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.registry import dispatch_tool, get_tool_schemas

__all__ = [
    "run_turn",
    "brief_args",
    "TrackingItem",
    "OpenRouterClient",
    "OpenRouterError",
    "dispatch_tool",
    "get_tool_schemas",
    "compact_thread_if_needed",
    "_run_loop",
    "_precheck_tool",
    "_handle_request_permissions",
    "_handle_request_user_input",
    "_run_post_patch_test",
    "_finalize_cancelled",
    "_budget_kill_turn",
    "_approval_diff_preview_for_tool",
]


def run_turn(*args, **kwargs):
    """Run one agent turn (delegates to agent.turn.lifecycle)."""
    from agent.turn.lifecycle import run_turn as _impl

    return _impl(*args, **kwargs)
