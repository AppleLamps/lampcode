from __future__ import annotations

from dataclasses import dataclass, field

from approval.cache import ApprovalCache


@dataclass
class HarnessSession:
    """In-memory session state for a thread run."""

    session_auto_approve: bool = False
    session_banner_shown: bool = False
    mcp_connected: bool = False
    ssh_command_approved: bool = False
    sync_push_approved: bool = False
    sync_pushed_this_turn: bool = False
    allow_network: bool = False
    allow_write_outside_cwd: bool = False
    allow_full_access: bool = False
    turn_allow_network: bool = False
    turn_allow_write_outside_cwd: bool = False
    turn_allow_full_access: bool = False
    tool_warn_emitted: bool = False
    turn_cost_usd: float = 0.0
    budget_exceeded: bool = False
    approval_cache: ApprovalCache = field(default_factory=ApprovalCache)

    def enable_session_auto_approve(self) -> None:
        self.session_auto_approve = True

    @property
    def auto_approve_active(self) -> bool:
        return self.session_auto_approve

    def grant_permission(self, scope: str, *, duration: str = "turn") -> None:
        target_session = duration == "session"
        if scope == "network":
            if target_session:
                self.allow_network = True
            else:
                self.turn_allow_network = True
        elif scope == "write_outside_cwd":
            if target_session:
                self.allow_write_outside_cwd = True
            else:
                self.turn_allow_write_outside_cwd = True
        elif scope == "full_access":
            if target_session:
                self.allow_full_access = True
            else:
                self.turn_allow_full_access = True

    def clear_turn_escalations(self) -> None:
        self.turn_allow_network = False
        self.turn_allow_write_outside_cwd = False
        self.turn_allow_full_access = False

    def has_network_escalation(self) -> bool:
        return self.allow_network or self.turn_allow_network or self.allow_full_access or self.turn_allow_full_access

    def has_full_access_escalation(self) -> bool:
        return self.allow_full_access or self.turn_allow_full_access
