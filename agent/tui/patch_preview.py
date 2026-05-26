"""Patch preview helpers for approvals and TUI cells."""

from __future__ import annotations

from pathlib import Path

from agent.tui.cells.base import FileChange
from agent.tui.diff_render import format_file_summary
from tools.patch import preview_patch


def patch_text_from_arguments(arguments: dict) -> str:
    return str(arguments.get("patch") or arguments.get("input") or "")


def patch_approval_diff_preview(
    arguments: dict,
    *,
    cwd: Path,
    max_preview_lines: int = 12,
) -> str | None:
    """Build unified diff preview text for approval banner (no disk writes)."""
    patch_text = patch_text_from_arguments(arguments)
    if not patch_text.strip():
        return None
    previews, err = preview_patch(patch_text, cwd=cwd, max_preview_lines=max_preview_lines)
    if err:
        return f"[dim]invalid patch: {err}[/dim]"
    if not previews:
        return None
    lines: list[str] = []
    summary = format_file_summary([(op.path, op.change_type) for op in previews])
    if summary:
        lines.append(summary)
    for op in previews:
        if op.diff_preview:
            lines.extend(op.diff_preview.splitlines())
    return "\n".join(lines)[:1200]


def patch_files_from_arguments(arguments: dict) -> list[FileChange]:
    patch_text = patch_text_from_arguments(arguments)
    if not patch_text.strip():
        return []
    previews, err = preview_patch(patch_text)
    if err:
        return []
    return [FileChange(path=op.path, change_type=op.change_type) for op in previews]
