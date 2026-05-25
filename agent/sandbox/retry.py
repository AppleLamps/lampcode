from __future__ import annotations

from agent.session import HarnessSession

TRUNCATION_SUFFIX = "\n[... output truncated ...]"


def truncate_shell_output(output: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(output) <= max_chars:
        return output, False
    keep = max(0, max_chars - len(TRUNCATION_SUFFIX))
    return output[:keep] + TRUNCATION_SUFFIX, True


def apply_sandbox_escalation_for_reason(session: HarnessSession, reason: str) -> None:
    lower = reason.lower()
    if "network" in lower:
        session.grant_permission("network", duration="turn")
    elif "outside workspace" in lower or "write target" in lower:
        session.grant_permission("write_outside_cwd", duration="turn")
