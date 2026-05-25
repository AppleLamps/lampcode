from __future__ import annotations

from pathlib import Path

from agent.paths import resolve_path_within_cwd


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
    resolved = resolve_path_within_cwd(cwd, path)
    if not resolved.is_file():
        return f"Error: file not found: {path}"

    try:
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Error reading file: {exc}"

    start = (offset or 1) - 1
    if start < 0:
        start = 0
    end = start + limit if limit else len(lines)
    selected = lines[start:end]

    numbered = []
    for i, line in enumerate(selected, start=start + 1):
        numbered.append(f"{i:6d}|{line}")
    return "\n".join(numbered) if numbered else "(empty file)"


def write_file(cwd: Path, path: str, content: str) -> str:
    resolved = resolve_path_within_cwd(cwd, path)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        line_count = len(content.splitlines())
        if content and not content.endswith("\n"):
            line_count = max(line_count, 1)
        return f"Successfully wrote {line_count} lines to {path}"
    except OSError as exc:
        return f"Error writing file: {exc}"
