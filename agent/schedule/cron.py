from __future__ import annotations

import datetime as dt
from typing import Any


def parse_cron_field(field: str, min_val: int, max_val: int) -> set[int]:
    if field == "*":
        return set(range(min_val, max_val + 1))
    if field.startswith("*/"):
        step = int(field[2:])
        return set(range(min_val, max_val + 1, step))
    if "," in field:
        return {int(x) for x in field.split(",")}
    if "-" in field:
        a, b = field.split("-", 1)
        return set(range(int(a), int(b) + 1))
    return {int(field)}


def cron_matches(cron: str, when: dt.datetime | None = None) -> bool:
    """Simple 5-field cron: minute hour dom month dow."""
    parts = cron.strip().split()
    if len(parts) != 5:
        return False
    now = when or dt.datetime.now()
    minute, hour, dom, month, dow = parts
    if now.minute not in parse_cron_field(minute, 0, 59):
        return False
    if now.hour not in parse_cron_field(hour, 0, 23):
        return False
    if now.day not in parse_cron_field(dom, 1, 31):
        return False
    if (now.month) not in parse_cron_field(month, 1, 12):
        return False
    weekday = (now.weekday() + 1) % 7  # 0=Sunday
    if weekday not in parse_cron_field(dow, 0, 6):
        return False
    return True


def is_job_due(job: Any, now: dt.datetime | None = None) -> bool:
    if not getattr(job, "enabled", True):
        return False
    if not cron_matches(job.cron, now):
        return False
    last = getattr(job, "last_run_at", 0.0) or 0.0
    if last <= 0:
        return True
    current = (now or dt.datetime.now()).timestamp()
    return (current - last) >= 55
