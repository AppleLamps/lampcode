"""Split agent/loop.py into agent/turn/* modules. Run from agent-cli/."""
from __future__ import annotations

from pathlib import Path

ROOT = Path("agent")
LOOP_PATH = ROOT / "loop.py"
lines = LOOP_PATH.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


RENAMES = {
    "_approval_diff_preview_for_tool": "approval_diff_preview_for_tool",
    "_finalize_cancelled": "finalize_cancelled",
    "_cost_cap_kill_turn": "cost_cap_kill_turn",
    "_budget_kill_turn": "budget_kill_turn",
    "_run_pre_turn_context_guard": "run_pre_turn_context_guard",
    "_rebuild_messages": "rebuild_messages",
    "_spill_item_output": "spill_item_output",
    "_create_tracking_items": "create_tracking_items",
    "_tool_completed_extra": "tool_completed_extra",
    "_mark_denied": "mark_denied",
    "_mark_approved": "mark_approved",
    "_apply_dispatch_results": "apply_dispatch_results",
    "_prompt_with_hooks": "prompt_with_hooks",
    "_exec_policy_block": "exec_policy_block",
    "_precheck_tool": "precheck_tool",
    "_handle_blocked_tool": "handle_blocked_tool",
    "_emit_execution_events": "emit_execution_events",
    "_sync_worker_items": "sync_worker_items",
    "_run_lsp_diagnostics_after_patch": "run_lsp_diagnostics_after_patch",
    "_run_post_patch_test": "run_post_patch_test",
    "_handle_request_user_input": "handle_request_user_input",
    "_handle_request_permissions": "handle_request_permissions",
    "_run_parallel_read_tool_round": "run_parallel_read_tool_round",
    "_run_loop": "run_loop",
}


def rename_body(body: str) -> str:
    for old, new in RENAMES.items():
        body = body.replace(f"def {old}", f"def {new}")
        body = body.replace(old + "(", new + "(")
    return body


def write_module(rel: str, header: str, start: int, end: int) -> None:
    body = rename_body(slice_lines(start, end))
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")
    print(f"wrote {rel} ({end - start + 1} lines)")


(ROOT / "turn").mkdir(exist_ok=True)
(ROOT / "turn" / "tools").mkdir(exist_ok=True)

write_module(
    "turn/types.py",
    '''"""Turn tool-tracking item types."""
from __future__ import annotations

from agent.models import (
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    WebSearchItem,
)

''',
    84,
    91,
)

write_module(
    "turn/helpers.py",
    '''"""Turn helper utilities."""
from __future__ import annotations

from agent.config import Config

''',
    68,
    75,
)
helpers_extra = rename_body(slice_lines(2052, 2071))
(ROOT / "turn/helpers.py").write_text(
    (ROOT / "turn/helpers.py").read_text(encoding="utf-8") + "\n" + helpers_extra,
    encoding="utf-8",
)

write_module(
    "turn/finalize.py",
    '''"""Turn cancellation finalization."""
from __future__ import annotations

from agent.config import Config
from agent.events import EventEmitter
from agent.models import AgentMessageItem, Thread, Turn
from agent.store import ThreadStore

''',
    1583,
    1607,
)

write_module(
    "turn/budget.py",
    '''"""Turn budget and cost-cap termination."""
from __future__ import annotations

from agent.cancel import CancelToken
from agent.events import EventEmitter
from agent.models import AgentMessageItem, Thread, Turn
from agent.multi_agent.registry import WorkerRegistry
from agent.store import ThreadStore

''',
    2110,
    2178,
)

write_module(
    "turn/context_guard.py",
    '''"""Pre-turn context compaction guard."""
from __future__ import annotations

from agent.compaction import compact_thread_if_needed, compact_tool_outputs
from agent.config import Config
from agent.context import build_thread_messages
from agent.context_meter import build_context_snapshot, invalidate_context_cache
from agent.models import Thread
from agent.store import ThreadStore

''',
    1395,
    1496,
)

write_module(
    "turn/hooks_bridge.py",
    '''"""Hooks integration for tool approvals."""
from __future__ import annotations

from agent.config import Config
from agent.models import Thread, Turn
from agent.session import HarnessSession
from approval.gate import TurnApprovalState, prompt_approval

''',
    1848,
    1876,
)

write_module(
    "turn/tools/tracking.py",
    '''"""Tool call tracking items for thread store."""
from __future__ import annotations

from typing import Any

from agent.config import Config
from agent.mcp.manager import McpManager
from agent.models import (
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    WebSearchItem,
)
from agent.turn.types import TrackingItem

''',
    1267,
    1392,
)

write_module(
    "turn/tools/dispatch.py",
    '''"""Tool dispatch result application and sandbox prechecks."""
from __future__ import annotations

from typing import Any

from agent.config import Config
from agent.events import EventEmitter
from agent.mcp.manager import McpManager
from agent.models import (
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    Thread,
    Turn,
    WebSearchItem,
)
from agent.sandbox.enforcer import (
    check_apply_patch,
    check_mcp_tool,
    check_run_command,
    check_write_file,
)
from agent.sandbox.retry import apply_sandbox_escalation_for_reason
from agent.session import HarnessSession
from agent.store import ThreadStore
from agent.turn.context_guard import spill_item_output
from agent.turn.types import TrackingItem
from approval.gate import exec_policy_block_reason
from tools.registry import DispatchResult

''',
    1499,
    1580,
)

# emit_execution through sync_worker (1979-2049) - append to dispatch
dispatch_extra = rename_body(slice_lines(1879, 2031))
(ROOT / "turn/tools/dispatch.py").write_text(
    (ROOT / "turn/tools/dispatch.py").read_text(encoding="utf-8") + dispatch_extra,
    encoding="utf-8",
)

write_module(
    "turn/tools/post_patch.py",
    '''"""Post-patch harness hooks (LSP diagnostics, tests)."""
from __future__ import annotations

from pathlib import Path

from agent.mcp.manager import McpManager

''',
    2074,
    2107,
)

write_module(
    "turn/special_tools.py",
    '''"""Special builtin tools: user input and permission escalation."""
from __future__ import annotations

import json
from typing import Any

from agent.config import Config
from agent.events import EventEmitter
from agent.models import Thread, Turn, UserInputItem
from agent.session import HarnessSession
from agent.store import ThreadStore
from approval.gate import (
    TurnApprovalState,
    format_tool_summary,
    needs_approval_prompt,
    prompt_approval,
)

''',
    2181,
    2313,
)

# parallel round
parallel_header = '''"""Parallel read-only tool round execution."""
from __future__ import annotations

from typing import Any

from agent.cancel import CancelToken
from agent.config import Config
from agent.events import EventEmitter
from agent.mcp.manager import McpManager
from agent.models import Thread, Turn
from agent.session import HarnessSession
from agent.store import ThreadStore
from agent.tool_round import run_parallel_tool_dispatches
from agent.turn.helpers import approval_diff_preview_for_tool
from agent.turn.hooks_bridge import prompt_with_hooks
from agent.turn.tools.dispatch import (
    apply_dispatch_results,
    emit_execution_events,
    exec_policy_block,
    handle_blocked_tool,
    precheck_tool,
)
from agent.turn.tools.tracking import (
    create_tracking_items,
    mark_approved,
    mark_denied,
    tool_completed_extra,
)
from approval.gate import (
    TurnApprovalState,
    format_tool_summary,
    needs_approval_prompt,
    tool_requires_approval,
)
from tools.registry import dispatch_tool, parse_tool_arguments, tool_requires_approval as registry_tool_requires_approval

'''
# Fix duplicate import - tool_requires_approval from registry
parallel_header = parallel_header.replace(
    "from approval.gate import (\n    TurnApprovalState,\n    format_tool_summary,\n    needs_approval_prompt,\n    tool_requires_approval,\n)\nfrom tools.registry import dispatch_tool, parse_tool_arguments, tool_requires_approval as registry_tool_requires_approval\n",
    "from approval.gate import TurnApprovalState, format_tool_summary, needs_approval_prompt\nfrom tools.registry import dispatch_tool, parse_tool_arguments, tool_requires_approval\n",
)

write_module("turn/tools/parallel.py", parallel_header, 1610, 1845)

# state machine
sm_header = '''"""Turn state machine: model rounds, tool calls, compaction."""
from __future__ import annotations

import json
from typing import Any, Callable

from agent.cancel import CancelToken, CancelledError
from agent.compaction import compact_thread_if_needed
from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens
from agent.context_meter import invalidate_context_cache
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
)
from agent.turn.tools.parallel import run_parallel_read_tool_round
from agent.turn.tools.post_patch import run_lsp_diagnostics_after_patch, run_post_patch_test
from agent.turn.tools.tracking import (
    create_tracking_items,
    mark_approved,
    mark_denied,
    tool_completed_extra,
)
from agent.store import ThreadStore
from approval.gate import (
    TurnApprovalState,
    format_tool_summary,
    needs_approval_prompt,
    tool_requires_approval,
)
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.registry import (
    DispatchResult,
    dispatch_tool,
    get_tool_schemas,
    parse_tool_arguments,
)

'''
write_module("turn/state_machine.py", sm_header, 483, 1264)

# lifecycle
lc_header = '''"""Turn lifecycle: setup, run state machine, teardown."""
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
from model.openrouter import OpenRouterClient
from tools.registry import get_tool_schemas

'''
write_module("turn/lifecycle.py", lc_header, 94, 480)

print("split_loop.py finished")
