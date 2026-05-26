"""Hold back markdown pipe-tables until complete (Codex-style streaming)."""

from __future__ import annotations


def _is_table_delimiter(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    inner = stripped.replace("|", "").replace(" ", "").replace("\t", "")
    return bool(inner) and set(inner) <= {"-", ":"}


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|")


def _table_block_complete(lines: list[str], start: int) -> bool:
    """True when the table starting at start has a complete final row."""
    if start >= len(lines):
        return True
    body = lines[start:]
    if len(body) < 2:
        return False
    if not (_is_table_row(body[0]) and _is_table_delimiter(body[1])):
        return False
    last = body[-1].strip()
    if not _is_table_row(last):
        return False
    return last.endswith("|")


def table_holdback_suffix(full_text: str) -> str:
    """Return suffix of an in-progress markdown table, or empty if none."""
    if not full_text.strip():
        return ""
    lines = full_text.split("\n")
    start = -1
    for i in range(len(lines) - 1):
        if _is_table_row(lines[i]) and _is_table_delimiter(lines[i + 1]):
            start = i
    if start < 0:
        if lines and _is_table_row(lines[-1]) and not lines[-1].strip().endswith("|"):
            return lines[-1]
        return ""
    if _table_block_complete(lines, start):
        return ""
    return "\n".join(lines[start:])
