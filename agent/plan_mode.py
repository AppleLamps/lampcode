from __future__ import annotations

import re

PROPOSED_PLAN_PATTERN = re.compile(
    r"<proposed_plan>(.*?)</proposed_plan>",
    re.IGNORECASE | re.DOTALL,
)


def parse_proposed_plan(text: str) -> tuple[str, str | None]:
    match = PROPOSED_PLAN_PATTERN.search(text)
    if not match:
        return text, None
    plan_body = match.group(1).strip()
    stripped = PROPOSED_PLAN_PATTERN.sub("", text).strip()
    return stripped, plan_body or None


def plan_summary(plan_text: str, *, max_len: int = 120) -> str:
    line = plan_text.splitlines()[0].strip() if plan_text else ""
    if len(line) > max_len:
        return line[: max_len - 3] + "..."
    return line
