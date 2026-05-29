from __future__ import annotations

from pathlib import Path

from agent.paths import resolve_path_within_cwd

DEFAULT_READ_LINE_LIMIT = 400


def truncate_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = f"\n\n[... truncated {len(text) - max_chars} chars ...]"
    keep = max_chars - len(marker)
    return text[:keep] + marker


def read_file(
    cwd: Path,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    try:
        resolved = resolve_path_within_cwd(cwd, path)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.is_file():
        return f"Error: file not found: {path}"

    try:
        stat = resolved.stat()
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Error reading file: {exc}"

    start = (offset or 1) - 1
    if start < 0:
        start = 0
    effective_limit = limit if limit is not None else DEFAULT_READ_LINE_LIMIT
    end = start + effective_limit
    selected = lines[start:end]
    truncated = limit is None and len(lines) > end

    numbered = []
    for i, line in enumerate(selected, start=start + 1):
        numbered.append(f"{i:6d}|{line}")
    if not numbered:
        body = "(empty file)"
    else:
        body = "\n".join(numbered)
    if truncated:
        remaining = len(lines) - end
        body += (
            f"\n\n[... {remaining} more lines omitted; file is {_format_size(stat.st_size)}; "
            f"use offset/limit to read more ...]"
        )
    return body


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def write_file(cwd: Path, path: str, content: str) -> str:
    try:
        resolved = resolve_path_within_cwd(cwd, path)
    except ValueError as exc:
        return f"Error: {exc}"
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        line_count = len(content.splitlines())
        if content and not content.endswith("\n"):
            line_count = max(line_count, 1)
        return f"Successfully wrote {line_count} lines to {path}"
    except OSError as exc:
        return f"Error writing file: {exc}"
