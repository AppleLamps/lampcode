from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.paths import resolve_path_within_cwd


@dataclass
class PatchResult:
    path: str
    change_type: str  # update | add | delete
    summary: str
    diff_snippet: str = ""


@dataclass
class ApplyPatchOutcome:
    results: list[PatchResult] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def apply_patch(cwd: Path, patch_text: str, *, max_snippet: int = 500) -> ApplyPatchOutcome:
    try:
        operations = parse_patch(patch_text)
    except ValueError as exc:
        return ApplyPatchOutcome(error=str(exc))

    results: list[PatchResult] = []
    for op in operations:
        try:
            result = _apply_operation(cwd, op, max_snippet=max_snippet)
            results.append(result)
        except ValueError as exc:
            return ApplyPatchOutcome(results=results, error=str(exc))

    if not results:
        return ApplyPatchOutcome(error="Patch contained no operations.")
    return ApplyPatchOutcome(results=results)


@dataclass
class _UpdateOp:
    path: str
    hunks: list[list[tuple[str, str | None]]]


@dataclass
class _AddOp:
    path: str
    lines: list[str]


@dataclass
class _DeleteOp:
    path: str


def parse_patch(patch_text: str) -> list[_UpdateOp | _AddOp | _DeleteOp]:
    text = patch_text.strip()
    if "*** Begin Patch" not in text:
        raise ValueError("Patch must start with '*** Begin Patch'")
    if "*** End Patch" not in text:
        raise ValueError("Patch must end with '*** End Patch'")

    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "*** Begin Patch")
    end = next(i for i, ln in enumerate(lines) if ln.strip() == "*** End Patch")
    body = lines[start + 1 : end]

    operations: list[_UpdateOp | _AddOp | _DeleteOp] = []
    i = 0
    while i < len(body):
        line = body[i].strip()
        if not line:
            i += 1
            continue

        if line.startswith("*** Update File:"):
            path = line.split(":", 1)[1].strip()
            i += 1
            hunks: list[list[tuple[str, str | None]]] = []
            current_hunk: list[tuple[str, str | None]] = []
            while i < len(body) and not body[i].strip().startswith("***"):
                hline = body[i]
                if hline.strip() == "@@":
                    if current_hunk:
                        hunks.append(current_hunk)
                        current_hunk = []
                elif hline.startswith("-") or hline.startswith("+"):
                    current_hunk.append((hline[0], hline[1:]))
                elif hline.startswith(" "):
                    current_hunk.append((" ", hline[1:]))
                else:
                    raise ValueError(
                        f"Malformed hunk line in update for {path} at body line {i + 1}: {hline!r}"
                    )
                i += 1
            if current_hunk:
                hunks.append(current_hunk)
            if not hunks:
                raise ValueError(f"Update patch for {path} has no hunks")
            operations.append(_UpdateOp(path=path, hunks=hunks))
            continue

        if line.startswith("*** Add File:"):
            path = line.split(":", 1)[1].strip()
            i += 1
            add_lines: list[str] = []
            while i < len(body) and not body[i].strip().startswith("***"):
                hline = body[i]
                if not hline.startswith("+"):
                    raise ValueError(
                        f"Add file line must start with '+' in {path}: {hline!r}"
                    )
                add_lines.append(hline[1:])
                i += 1
            operations.append(_AddOp(path=path, lines=add_lines))
            continue

        if line.startswith("*** Delete File:"):
            path = line.split(":", 1)[1].strip()
            operations.append(_DeleteOp(path=path))
            i += 1
            continue

        raise ValueError(f"Unknown patch section: {line}")

    return operations


def _apply_operation(
    cwd: Path, op: _UpdateOp | _AddOp | _DeleteOp, *, max_snippet: int
) -> PatchResult:
    if isinstance(op, _AddOp):
        resolved = resolve_path_within_cwd(cwd, op.path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(op.lines)
        if content:
            content += "\n"
        resolved.write_text(content, encoding="utf-8")
        snippet = "\n".join(f"+{ln}" for ln in op.lines[:20])
        return PatchResult(
            path=op.path,
            change_type="add",
            summary=f"added {len(op.lines)} lines to {op.path}",
            diff_snippet=_truncate(snippet, max_snippet),
        )

    if isinstance(op, _DeleteOp):
        resolved = resolve_path_within_cwd(cwd, op.path)
        if not resolved.is_file():
            raise ValueError(f"Cannot delete missing file: {op.path}")
        resolved.unlink()
        return PatchResult(
            path=op.path,
            change_type="delete",
            summary=f"deleted {op.path}",
            diff_snippet=f"--- {op.path} (deleted)",
        )

    resolved = resolve_path_within_cwd(cwd, op.path)
    if not resolved.is_file():
        raise ValueError(
            f"Cannot update missing file: {op.path}. Use read_file to verify the path."
        )

    file_lines = resolved.read_text(encoding="utf-8").splitlines()
    changes = 0
    diff_lines: list[str] = []

    for hunk_idx, hunk in enumerate(op.hunks, start=1):
        file_lines, hunk_changes, hunk_diff = _apply_hunk(
            file_lines, op.path, hunk_idx, hunk
        )
        changes += hunk_changes
        diff_lines.extend(hunk_diff)

    resolved.write_text(
        "\n".join(file_lines) + ("\n" if file_lines else ""), encoding="utf-8"
    )
    return PatchResult(
        path=op.path,
        change_type="update",
        summary=f"updated {changes} line(s) in {op.path}",
        diff_snippet=_truncate("\n".join(diff_lines), max_snippet),
    )


def _apply_hunk(
    file_lines: list[str],
    path: str,
    hunk_idx: int,
    hunk: list[tuple[str, str | None]],
) -> tuple[list[str], int, list[str]]:
    """Apply a single hunk using context lines; returns updated lines."""
    pos = _find_hunk_position(file_lines, path, hunk_idx, hunk)
    changes = 0
    diff_lines: list[str] = []
    i = 0
    while i < len(hunk):
        prefix, content = hunk[i]
        line_no = pos + 1

        if prefix == " ":
            if pos >= len(file_lines) or file_lines[pos] != (content or ""):
                raise ValueError(
                    f"Context mismatch in {path} hunk {hunk_idx} at line {line_no}: "
                    f"expected {content!r}, got {file_lines[pos] if pos < len(file_lines) else '<eof>'!r}"
                )
            pos += 1
            i += 1
            continue

        if prefix == "-":
            old_line = content or ""
            new_line: str | None = None
            if i + 1 < len(hunk) and hunk[i + 1][0] == "+":
                new_line = hunk[i + 1][1] or ""
                i += 1
            if pos >= len(file_lines) or file_lines[pos] != old_line:
                raise ValueError(
                    f"Could not apply hunk {hunk_idx} in {path} at line {line_no}: "
                    f"expected to remove {old_line!r}, found {file_lines[pos] if pos < len(file_lines) else '<eof>'!r}. "
                    f"Use read_file then retry."
                )
            diff_lines.append(f"-{old_line}")
            if new_line is not None:
                file_lines[pos] = new_line
                diff_lines.append(f"+{new_line}")
            else:
                file_lines.pop(pos)
                pos -= 1
            changes += 1
            pos += 1
            i += 1
            continue

        if prefix == "+":
            new_line = content or ""
            file_lines.insert(pos, new_line)
            diff_lines.append(f"+{new_line}")
            changes += 1
            pos += 1
            i += 1
            continue

        i += 1

    return file_lines, changes, diff_lines


def _find_hunk_position(
    file_lines: list[str],
    path: str,
    hunk_idx: int,
    hunk: list[tuple[str, str | None]],
) -> int:
    """Find starting line index for hunk using first removable/context line."""
    anchors: list[str] = []
    for prefix, content in hunk:
        if prefix in ("-", " "):
            anchors.append(content or "")

    if not anchors:
        return len(file_lines)

    first = anchors[0]
    candidates = [i for i, ln in enumerate(file_lines) if ln == first]
    if not candidates:
        raise ValueError(
            f"Could not locate hunk {hunk_idx} in {path}: anchor line {first!r} not found. "
            f"Use read_file then retry."
        )

    for start in candidates:
        if _hunk_matches_at(file_lines, start, hunk):
            return start

    raise ValueError(
        f"Could not match hunk {hunk_idx} context in {path} near line {candidates[0] + 1}. "
        f"Use read_file then retry."
    )


def _hunk_matches_at(
    file_lines: list[str], start: int, hunk: list[tuple[str, str | None]]
) -> bool:
    pos = start
    for prefix, content in hunk:
        if prefix == "+":
            continue
        if pos >= len(file_lines):
            return False
        if file_lines[pos] != (content or ""):
            return False
        pos += 1
    return True


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n[... truncated ...]"


@dataclass
class PatchPreviewOp:
    path: str
    change_type: str
    additions: int = 0
    deletions: int = 0
    diff_preview: str = ""


def preview_patch(
    patch_text: str,
    *,
    cwd: Path | None = None,
    max_preview_lines: int = 8,
) -> tuple[list[PatchPreviewOp], str | None]:
    """Parse patch and return per-file preview stats without applying."""
    try:
        operations = parse_patch(patch_text)
    except ValueError as exc:
        return [], str(exc)

    previews: list[PatchPreviewOp] = []
    for op in operations:
        if isinstance(op, _AddOp):
            adds = len(op.lines)
            diff = "\n".join(f"+{ln}" for ln in op.lines[:max_preview_lines])
            previews.append(
                PatchPreviewOp(
                    path=op.path,
                    change_type="add",
                    additions=adds,
                    deletions=0,
                    diff_preview=_truncate(diff, 400),
                )
            )
        elif isinstance(op, _DeleteOp):
            previews.append(
                PatchPreviewOp(
                    path=op.path,
                    change_type="delete",
                    additions=0,
                    deletions=1,
                    diff_preview=f"--- {op.path} (delete)",
                )
            )
        elif isinstance(op, _UpdateOp):
            adds = sum(1 for h in op.hunks for p, _ in h if p == "+")
            dels = sum(1 for h in op.hunks for p, _ in h if p == "-")
            diff_lines: list[str] = []
            for hunk in op.hunks:
                for prefix, content in hunk:
                    if prefix in ("+", "-"):
                        diff_lines.append(f"{prefix}{content or ''}")
                    if len(diff_lines) >= max_preview_lines:
                        break
                if len(diff_lines) >= max_preview_lines:
                    break
            note = ""
            if cwd is not None:
                resolved = resolve_path_within_cwd(cwd, op.path)
                if not resolved.is_file():
                    note = " (file missing — read_file first)"
            previews.append(
                PatchPreviewOp(
                    path=op.path + note,
                    change_type="update",
                    additions=adds,
                    deletions=dels,
                    diff_preview=_truncate("\n".join(diff_lines), 400),
                )
            )
    return previews, None


def format_patch_brief(patch_text: str, *, max_files: int = 3) -> str:
    """One-line summary for tool pending / approval prompts."""
    previews, err = preview_patch(patch_text)
    if err:
        return f"apply_patch: invalid ({err[:60]})"
    if not previews:
        return "apply_patch: (empty)"
    parts: list[str] = []
    for op in previews[:max_files]:
        stats = f"+{op.additions}/-{op.deletions}"
        parts.append(f"{op.path} ({op.change_type}, {stats})")
    extra = len(previews) - max_files
    suffix = f" +{extra} more" if extra > 0 else ""
    return "apply_patch: " + ", ".join(parts) + suffix


def format_patch_preview_block(patch_text: str, *, max_preview_lines: int = 6) -> str:
    """Multi-line preview for approvals and stderr output."""
    previews, err = preview_patch(patch_text, max_preview_lines=max_preview_lines)
    if err:
        return f"Patch error: {err}"
    if not previews:
        return "Patch: (no operations)"
    lines: list[str] = ["Patch preview:"]
    for op in previews:
        lines.append(
            f"  • {op.path} [{op.change_type}] +{op.additions}/-{op.deletions}"
        )
        if op.diff_preview:
            for ln in op.diff_preview.splitlines()[:max_preview_lines]:
                lines.append(f"    {ln}")
    return "\n".join(lines)
