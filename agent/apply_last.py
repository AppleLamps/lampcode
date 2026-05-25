from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent.models import FileChangeItem, Thread
from agent.store import ThreadStore
from tools.patch import ApplyPatchOutcome, apply_patch, format_patch_preview_block


@dataclass
class ApplyLastResult:
    thread_id: str
    turn_id: str | None = None
    patches: list[str] = field(default_factory=list)
    preview_lines: list[str] = field(default_factory=list)
    outcome: ApplyPatchOutcome | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.outcome is None or self.outcome.ok)


def _patch_from_file_change(item: FileChangeItem) -> str | None:
    if not item.tool_arguments:
        return None
    try:
        data = json.loads(item.tool_arguments)
    except json.JSONDecodeError:
        return None
    patch = data.get("patch")
    return patch if isinstance(patch, str) and patch.strip() else None


def collect_patches_from_turn(turn) -> list[str]:
    patches: list[str] = []
    for item in turn.items:
        if item.type != "fileChange":
            continue
        if not isinstance(item, FileChangeItem):
            continue
        patch = _patch_from_file_change(item)
        if patch:
            patches.append(patch)
    return patches


def find_last_patch_turn(thread: Thread):
    for turn in reversed(thread.turns):
        if turn.status != "completed":
            continue
        patches = collect_patches_from_turn(turn)
        if patches:
            return turn, patches
    return None, []


def apply_last_from_thread(
    thread: Thread,
    cwd: Path,
    *,
    dry_run: bool = False,
) -> ApplyLastResult:
    turn, patches = find_last_patch_turn(thread)
    if not patches:
        return ApplyLastResult(
            thread_id=thread.id,
            error="No completed patch found in thread history",
        )

    preview_lines: list[str] = []
    for patch_text in patches:
        preview_lines.append(format_patch_preview_block(patch_text))

    if dry_run:
        return ApplyLastResult(
            thread_id=thread.id,
            turn_id=turn.id if turn else None,
            patches=patches,
            preview_lines=preview_lines,
        )

    combined_outcome = ApplyPatchOutcome()
    for patch_text in patches:
        outcome = apply_patch(cwd, patch_text)
        if outcome.error:
            combined_outcome.error = outcome.error
            combined_outcome.results.extend(outcome.results)
            return ApplyLastResult(
                thread_id=thread.id,
                turn_id=turn.id if turn else None,
                patches=patches,
                preview_lines=preview_lines,
                outcome=combined_outcome,
                error=outcome.error,
            )
        combined_outcome.results.extend(outcome.results)

    return ApplyLastResult(
        thread_id=thread.id,
        turn_id=turn.id if turn else None,
        patches=patches,
        preview_lines=preview_lines,
        outcome=combined_outcome,
    )


def apply_last_for_thread_id(
    store: ThreadStore,
    thread_id: str,
    cwd: Path | None = None,
    *,
    dry_run: bool = False,
) -> ApplyLastResult:
    thread = store.load_thread(thread_id)
    target_cwd = cwd or Path(thread.cwd)
    return apply_last_from_thread(thread, target_cwd, dry_run=dry_run)
