from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HarnessSession:
    """In-memory session state for a thread run."""

    session_auto_approve: bool = False
    session_banner_shown: bool = False
    mcp_connected: bool = False
    ssh_command_approved: bool = False
    sync_push_approved: bool = False
    sync_pushed_this_turn: bool = False

    def enable_session_auto_approve(self) -> None:
        self.session_auto_approve = True

    @property
    def auto_approve_active(self) -> bool:
        return self.session_auto_approve
