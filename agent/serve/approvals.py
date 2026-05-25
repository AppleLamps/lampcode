from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PendingApproval:
    approval_id: str
    thread_id: str
    turn_id: str
    summary: str
    tool_name: str
    event: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None
    approved_by: str | None = None
    approved_by_role: str | None = None


class ApprovalRegistry:
    _instance: ApprovalRegistry | None = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    @classmethod
    def global_registry(cls) -> ApprovalRegistry:
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def create(
        self,
        *,
        thread_id: str,
        turn_id: str,
        summary: str,
        tool_name: str,
    ) -> PendingApproval:
        approval_id = f"appr-{uuid.uuid4().hex[:12]}"
        pending = PendingApproval(
            approval_id=approval_id,
            thread_id=thread_id,
            turn_id=turn_id,
            summary=summary,
            tool_name=tool_name,
        )
        with self._lock:
            self._pending[approval_id] = pending
        return pending

    def resolve(
        self,
        approval_id: str,
        decision: str,
        *,
        approved_by: str | None = None,
        approved_by_role: str | None = None,
    ) -> bool:
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return False
        pending.decision = decision
        pending.approved_by = approved_by
        pending.approved_by_role = approved_by_role
        pending.event.set()
        return True

    def wait(self, approval_id: str, timeout: float) -> str | None:
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return None
        pending.event.wait(timeout=timeout)
        return pending.decision

    def pop(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            return self._pending.pop(approval_id, None)


def map_api_decision(decision: str) -> str | None:
    d = decision.strip().lower()
    if d in ("accept", "y", "yes", "approve"):
        return "y"
    if d in ("deny", "n", "no", "reject"):
        return "n"
    if d in ("accept_turn", "a", "all"):
        return "a"
    if d in ("accept_session", "session"):
        return "A"
    return None


def make_http_approval_input(timeout_sec: int = 300) -> Callable[[str], str]:
    registry = ApprovalRegistry.global_registry()

    def _input(summary: str) -> str:
        # approval_id embedded by gate when using http bridge — fallback deny
        pending = next(
            (p for p in registry._pending.values() if p.summary == summary and not p.event.is_set()),
            None,
        )
        if pending is None:
            return "n"
        decision = registry.wait(pending.approval_id, timeout=timeout_sec)
        registry.pop(pending.approval_id)
        if decision is None:
            return "n"
        mapped = map_api_decision(decision)
        return mapped or "n"

    return _input
