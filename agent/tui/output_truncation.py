"""Codex-style tool/exec output truncation for TUI cells."""

from __future__ import annotations

DEFAULT_BYTE_BUDGET = 4096
DEFAULT_TOKEN_BUDGET = 1024
COLLAPSED_LINE_LIMIT = 8
EXPANDED_LINE_LIMIT = 200


def approx_token_budget_bytes(token_budget: int) -> int:
    """Rough UTF-8 byte cap from token count (~4 chars per token)."""
    return max(256, token_budget * 4)


def truncate_middle_chars(content: str, max_bytes: int) -> str:
    """Keep head and tail when UTF-8 byte length exceeds max_bytes."""
    raw = content.encode("utf-8")
    if len(raw) <= max_bytes:
        return content
    if max_bytes < 32:
        return content[: max_bytes // 4]

    marker = "\n... omitted ...\n"
    marker_bytes = len(marker.encode("utf-8"))
    head_budget = max_bytes // 2
    tail_budget = max_bytes - head_budget - marker_bytes
    if tail_budget < 8:
        tail_budget = 8
        head_budget = max(8, max_bytes - tail_budget - marker_bytes)

    head = raw[:head_budget].decode("utf-8", errors="ignore")
    tail = raw[-tail_budget:].decode("utf-8", errors="ignore")
    omitted_bytes = len(raw) - head_budget - tail_budget
    return f"{head}{marker}({omitted_bytes} bytes omitted){marker}{tail}"


def count_lines(content: str) -> int:
    if not content:
        return 0
    return len(content.splitlines())


def format_tool_output_lines(
    content: str,
    *,
    expanded: bool,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
    token_budget: int | None = DEFAULT_TOKEN_BUDGET,
) -> list[str]:
    """
    Return display lines for exec/tool output.

    When truncated, prefixes with ``Total output lines: N`` (Codex-style).
    """
    if not content:
        return []

    total_lines = count_lines(content)
    body = content
    effective_byte_budget = byte_budget
    if token_budget is not None:
        effective_byte_budget = min(byte_budget, approx_token_budget_bytes(token_budget))
    byte_truncated = len(body.encode("utf-8")) > effective_byte_budget
    if byte_truncated:
        body = truncate_middle_chars(body, effective_byte_budget)

    lines = body.splitlines()
    line_limit = EXPANDED_LINE_LIMIT if expanded else COLLAPSED_LINE_LIMIT
    line_truncated = len(lines) > line_limit

    if line_truncated:
        remaining = len(lines) - line_limit
        lines = lines[:line_limit] + [f"... ({remaining} more lines — press e to expand)"]

    if byte_truncated or line_truncated or (total_lines > line_limit and not expanded):
        header = f"Total output lines: {total_lines}"
        return [header, *lines]

    return lines
