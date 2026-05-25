from __future__ import annotations

from agent.sandbox.policy import SandboxMode


def kernel_enabled_for_mode(sandbox_mode: SandboxMode, *, kernel_enabled: bool) -> bool:
    if not kernel_enabled:
        return False
    if sandbox_mode == SandboxMode.DANGER_FULL_ACCESS:
        return False
    return True


def policy_label(sandbox_mode: SandboxMode) -> str:
    if sandbox_mode == SandboxMode.READ_ONLY:
        return "read-only"
    if sandbox_mode == SandboxMode.WORKSPACE_WRITE:
        return "workspace-write"
    return "none"
