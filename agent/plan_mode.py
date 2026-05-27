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


PLAN_MODE_SYSTEM_APPEND = """You are in **plan mode** (read-only exploration). Do not modify files, apply patches, or run mutating commands.

You may only use these tools: {allowed_tools}.

When you have a concrete implementation plan ready for the user to approve, include it in your reply wrapped exactly like this:

<proposed_plan>
# Plan title
1. Step one
2. Step two
</proposed_plan>

Keep investigating with read-only tools until the plan is actionable. Outside the tag, briefly note open questions or assumptions."""


def build_plan_system_append(allowed_tools: list[str]) -> str:
    tools = ", ".join(f"`{t}`" for t in allowed_tools) if allowed_tools else "(none)"
    return PLAN_MODE_SYSTEM_APPEND.format(allowed_tools=tools)
