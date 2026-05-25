from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.settings import ScheduleSettings


RISKY_ACK_PATH = Path.home() / ".agent-cli" / "schedule-unattended-acknowledged"


def validate_job_for_run(
    job: Any,
    settings: ScheduleSettings,
    *,
    config_multi_agent_budgets_enabled: bool = False,
) -> tuple[bool, str]:
    if not settings.enabled:
        return False, "schedule.enabled=false"
    if not settings.require_multi_agent and not job.multi_agent:
        return False, "scheduled jobs must use multi_agent=true"
    if settings.require_multi_agent and not job.multi_agent:
        return False, "multi_agent required for scheduled jobs"
    profile = (job.budget_profile or "off").lower()
    if settings.require_budgets:
        if profile == "off":
            return False, "budget_profile cannot be off for scheduled jobs"
        if not config_multi_agent_budgets_enabled and profile == "standard":
            pass  # profile_settings still applies at runtime
    if (settings.default_approval_mode or "interactive").lower() == "auto":
        if not settings.allow_unattended_auto:
            return False, "auto approval forbidden — set allow_unattended_auto=true"
        if not RISKY_ACK_PATH.is_file():
            return False, "doctor warning not acknowledged — create ~/.agent-cli/schedule-unattended-acknowledged"
    if job.require_approval and settings.default_approval_mode == "auto" and not settings.allow_unattended_auto:
        return False, "require_approval incompatible with auto mode"
    return True, ""


def acknowledge_unattended_risk() -> Path:
    RISKY_ACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    RISKY_ACK_PATH.write_text("acknowledged\n", encoding="utf-8")
    return RISKY_ACK_PATH
