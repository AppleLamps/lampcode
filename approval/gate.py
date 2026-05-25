from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Console

from agent.config import Config
from agent.exec_policy import ExecPolicyMode, evaluate_command
from agent.session import HarnessSession

console = Console()

_approval_input: Callable[[str], str] | None = None


@dataclass
class HttpApprovalBridge:
    thread_id: str
    turn_id: str
    timeout_sec: int = 300
    emit: Callable[[str, str, str], None] | None = None


_http_bridge: HttpApprovalBridge | None = None


def set_http_approval_bridge(bridge: HttpApprovalBridge | None) -> None:
    global _http_bridge
    _http_bridge = bridge


def set_approval_input(fn: Callable[[str], str] | None) -> None:
    global _approval_input
    _approval_input = fn


def parse_approval_response(
    response: str,
    *,
    turn_state: TurnApprovalState | None = None,
    session: HarnessSession | None = None,
) -> bool:
    if response == "A":
        if session:
            session.enable_session_auto_approve()
            if not session.session_banner_shown:
                session.session_banner_shown = True
        return True
    lower = response.lower()
    if lower in ("a", "all"):
        if turn_state:
            turn_state.approve_all = True
        return True
    return lower in ("y", "yes")

READ_COMMANDS = re.compile(
    r"^\s*(cat|type|head|tail|less|more|Get-Content|Get-ChildItem|ls|dir|findstr|select-string)\b",
    re.IGNORECASE,
)
WRITE_COMMANDS = re.compile(
    r"^\s*(rm|del|erase|remove-item|mv|move|move-item|copy|copy-item|"
    r"mkdir|md|new-item|Set-Content|Out-File|echo\s.*>\s|tee)\b",
    re.IGNORECASE,
)
TEST_COMMANDS = re.compile(
    r"^\s*(pytest|python\s+-m\s+pytest|npm\s+test|cargo\s+test|go\s+test|make\s+test)\b",
    re.IGNORECASE,
)


@dataclass
class TurnApprovalState:
    approve_all: bool = False
    denied: set[str] = field(default_factory=set)


def classify_command(cmd: str) -> str:
    cmd = cmd.strip()
    if READ_COMMANDS.search(cmd):
        return "read"
    if WRITE_COMMANDS.search(cmd):
        return "write/destructive"
    if TEST_COMMANDS.search(cmd):
        return "test/run"
    return "run"


def needs_approval_prompt(
    tool_name: str,
    arguments: dict[str, Any],
    config: Config,
    *,
    turn_state: TurnApprovalState | None = None,
    session: HarnessSession | None = None,
) -> bool:
    if config.auto_approve:
        return False
    if session and session.session_auto_approve:
        return False
    if turn_state and turn_state.approve_all:
        return False

    if config.exec_policy.mode == ExecPolicyMode.NEVER:
        return False

    if tool_name == "run_command" and config.execution.backend == "ssh":
        if session and not session.ssh_command_approved:
            return True

    if tool_name == "run_command" and config.exec_policy.mode == ExecPolicyMode.UNTRUSTED:
        cmd = arguments.get("cmd", "")
        result = evaluate_command(cmd, config.exec_policy)
        if result["decision"] == "deny":
            return True
        if result["auto_approve"]:
            return False

    return True


def exec_policy_block_reason(cmd: str, config: Config) -> str | None:
    result = evaluate_command(cmd.strip(), config.exec_policy)
    if result["decision"] == "deny":
        return f"Exec policy deny rule matched for command: {cmd!r}"
    return None


def prompt_approval(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    auto_approve: bool = False,
    turn_state: TurnApprovalState | None = None,
    session: HarnessSession | None = None,
) -> bool:
    if auto_approve:
        return True
    if session and session.session_auto_approve:
        return True
    if turn_state and turn_state.approve_all:
        return True

    summary = format_tool_summary(tool_name, arguments)
    from agent.telemetry import trace_span

    with trace_span("approval.wait", tool=tool_name):
        if _http_bridge and _http_bridge.emit:
            from agent.serve.approvals import ApprovalRegistry, map_api_decision

            pending = ApprovalRegistry.global_registry().create(
                thread_id=_http_bridge.thread_id,
                turn_id=_http_bridge.turn_id,
                summary=summary,
                tool_name=tool_name,
            )
            _http_bridge.emit(pending.approval_id, tool_name, summary)
            decision = ApprovalRegistry.global_registry().wait(
                pending.approval_id, timeout=_http_bridge.timeout_sec
            )
            ApprovalRegistry.global_registry().pop(pending.approval_id)
            if decision is None:
                with trace_span("approval.decision", tool=tool_name, decision="timeout"):
                    return False
            mapped = map_api_decision(decision)
            if mapped == "A":
                if session:
                    session.enable_session_auto_approve()
                with trace_span("approval.decision", tool=tool_name, decision="accept_session"):
                    return True
            if mapped == "a":
                if turn_state:
                    turn_state.approve_all = True
                with trace_span("approval.decision", tool=tool_name, decision="accept_turn"):
                    return True
            approved = mapped == "y"
            with trace_span("approval.decision", tool=tool_name, decision=mapped or "deny"):
                return approved

        if _approval_input:
            response = _approval_input(summary)
        else:
            console.print(f"[yellow][approval][/yellow] {summary}")
            console.print("[dim]Allow? [y/N/a=turn / A=session][/dim]", end=" ")

            try:
                response = input().strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                with trace_span("approval.decision", tool=tool_name, decision="interrupt"):
                    return False

        if response == "A":
            if session:
                session.enable_session_auto_approve()
                if not session.session_banner_shown:
                    if not _approval_input:
                        console.print("[dim][approval] session auto-approve enabled[/dim]")
                    session.session_banner_shown = True
            with trace_span("approval.decision", tool=tool_name, decision="accept_session"):
                return True

        result = parse_approval_response(response, turn_state=turn_state, session=session)
        with trace_span("approval.decision", tool=tool_name, decision="accept" if result else "deny"):
            return result


def format_tool_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        intent = classify_command(cmd)
        workdir = arguments.get("workdir")
        prefix = f"run_command [{intent}]"
        if workdir:
            return f"{prefix} in {workdir}: {cmd}"
        return f"{prefix}: {cmd}"

    if tool_name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        lines = len(content.splitlines())
        return f"write_file (overwrite): {path} ({lines} lines)"

    if tool_name == "apply_patch":
        from tools.patch import format_patch_brief, format_patch_preview_block

        patch = arguments.get("patch", "")
        brief = format_patch_brief(patch)
        preview = format_patch_preview_block(patch, max_preview_lines=4)
        if preview and preview not in brief:
            return f"{brief}\n{preview}"
        return brief

    if tool_name.startswith("mcp__"):
        return f"MCP tool {tool_name}: {arguments}"

    if tool_name == "read_file":
        return f"read_file: {arguments.get('path', '')}"

    if tool_name == "web_search":
        return f"web_search: {arguments.get('query', '')}"

    if tool_name == "sync_push":
        return arguments.get("summary", "sync push to remote workspace")

    if tool_name == "sync_conflict":
        return arguments.get("summary", f"sync conflict: {arguments.get('path', '')}")

    if tool_name == "spawn_worker":
        deps = arguments.get("depends_on") or []
        dep_note = f" deps={deps}" if deps else ""
        return f"spawn_worker: {arguments.get('task', '')[:80]}{dep_note}"

    if tool_name == "spawn_worker_batch":
        tasks = arguments.get("tasks") or []
        return f"spawn_worker_batch: {len(tasks)} tasks"

    if tool_name == "get_worker_graph":
        return "get_worker_graph"

    if tool_name == "wait_workers":
        ids = arguments.get("worker_ids")
        return f"wait_workers: {ids if ids else 'all'}"

    if tool_name == "list_workers":
        return "list_workers"

    parts = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return f"{tool_name}: {parts}"


def prompt_sync_conflict(
    path: str,
    *,
    auto_approve: bool = False,
    turn_state: TurnApprovalState | None = None,
    session: HarnessSession | None = None,
) -> str | None:
    """Return strategy: local-wins, remote-wins, skip, abort, or None if denied."""
    if auto_approve or (session and session.session_auto_approve):
        return "local-wins"
    if turn_state and turn_state.approve_all:
        return "local-wins"

    summary = f"sync conflict: {path}"
    if _approval_input:
        response = _approval_input(f"{summary} [l/r/s/a]")
    else:
        console.print(f"[yellow][sync conflict][/yellow] {path}")
        console.print("[dim]Resolve? [l=local-wins / r=remote-wins / s=skip / a=abort][/dim]", end=" ")
        try:
            response = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None

    key = response.strip().lower()
    if key in ("l", "local", "local-wins"):
        return "local-wins"
    if key in ("r", "remote", "remote-wins"):
        return "remote-wins"
    if key in ("s", "skip"):
        return "skip"
    if key in ("a", "abort"):
        return "abort"
    return None


def _extract_patch_files(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        line = line.strip()
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            if line.startswith(prefix):
                files.append(line.split(":", 1)[1].strip())
    return files
