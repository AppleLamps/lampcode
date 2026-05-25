from __future__ import annotations

from pathlib import Path

from agent.paths import resolve_path_within_cwd
from agent.sandbox.classifier import (
    CommandRisk,
    SandboxDecision,
    classify_command,
    extract_write_targets,
    mcp_tool_is_mutating,
)
from agent.sandbox.policy import SandboxMode


def check_run_command(
    cmd: str,
    cwd: Path,
    mode: SandboxMode,
) -> SandboxDecision:
    if mode == SandboxMode.DANGER_FULL_ACCESS:
        return SandboxDecision(allowed=True)

    risk = classify_command(cmd)

    if mode == SandboxMode.READ_ONLY:
        if risk in (CommandRisk.WRITE, CommandRisk.NETWORK):
            return SandboxDecision(
                allowed=False,
                reason=f"{risk.value}-like command denied in read-only sandbox",
            )
        return SandboxDecision(allowed=True)

    # workspace-write
    if risk == CommandRisk.NETWORK:
        return SandboxDecision(
            allowed=False,
            reason="network-like command denied in workspace-write sandbox",
        )
    if risk == CommandRisk.WRITE:
        for target in extract_write_targets(cmd):
            if not target or target in ("&1", "&2", "nul", "NUL"):
                continue
            try:
                resolve_path_within_cwd(cwd, target)
            except ValueError:
                return SandboxDecision(
                    allowed=False,
                    reason=f"write target outside workspace: {target!r}",
                )
    return SandboxDecision(allowed=True)


def check_mcp_tool(
    exposed_name: str,
    mode: SandboxMode,
    *,
    require_approval: bool = True,
) -> SandboxDecision:
    if mode == SandboxMode.DANGER_FULL_ACCESS:
        return SandboxDecision(allowed=True)

    if mode == SandboxMode.READ_ONLY and mcp_tool_is_mutating(exposed_name):
        if require_approval:
            return SandboxDecision(
                allowed=False,
                reason=f"mutating MCP tool denied in read-only sandbox: {exposed_name}",
            )
    return SandboxDecision(allowed=True)


def check_write_file(path: str, cwd: Path, mode: SandboxMode) -> SandboxDecision:
    if mode == SandboxMode.DANGER_FULL_ACCESS:
        return SandboxDecision(allowed=True)
    if mode == SandboxMode.READ_ONLY:
        return SandboxDecision(
            allowed=False,
            reason="file writes denied in read-only sandbox",
        )
    try:
        resolve_path_within_cwd(cwd, path)
    except ValueError as exc:
        return SandboxDecision(allowed=False, reason=str(exc))
    return SandboxDecision(allowed=True)


def check_apply_patch(cwd: Path, mode: SandboxMode) -> SandboxDecision:
    if mode == SandboxMode.READ_ONLY:
        return SandboxDecision(
            allowed=False,
            reason="patches denied in read-only sandbox",
        )
    return SandboxDecision(allowed=True)
