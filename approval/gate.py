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
    if _approval_input:
        response = _approval_input(summary)
    else:
        console.print(f"[yellow][approval][/yellow] {summary}")
        console.print("[dim]Allow? [y/N/a=turn / A=session][/dim]", end=" ")

        try:
            response = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return False

    if response == "A":
        if session:
            session.enable_session_auto_approve()
            if not session.session_banner_shown:
                if not _approval_input:
                    console.print("[dim][approval] session auto-approve enabled[/dim]")
                session.session_banner_shown = True
        return True

    return parse_approval_response(response, turn_state=turn_state, session=session)


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
        patch = arguments.get("patch", "")
        files = _extract_patch_files(patch)
        if files:
            return f"apply_patch: {', '.join(files)}"
        return "apply_patch: (see patch content)"

    if tool_name.startswith("mcp__"):
        return f"MCP tool {tool_name}: {arguments}"

    if tool_name == "read_file":
        return f"read_file: {arguments.get('path', '')}"

    if tool_name == "web_search":
        return f"web_search: {arguments.get('query', '')}"

    if tool_name == "spawn_worker":
        return f"spawn_worker: {arguments.get('task', '')[:80]}"

    if tool_name == "wait_workers":
        ids = arguments.get("worker_ids")
        return f"wait_workers: {ids if ids else 'all'}"

    if tool_name == "list_workers":
        return "list_workers"

    parts = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return f"{tool_name}: {parts}"


def _extract_patch_files(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        line = line.strip()
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            if line.startswith(prefix):
                files.append(line.split(":", 1)[1].strip())
    return files
