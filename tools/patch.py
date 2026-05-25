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
    hunks: list[list[tuple[str, str | None]]]  # (prefix, line) prefix is '-' '+' or ' '


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
    # Strip begin/end markers
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
                    raise ValueError(f"Malformed hunk line in update for {path}: {hline!r}")
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
                    raise ValueError(f"Add file lines must start with '+': {hline!r}")
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

    # Update
    resolved = resolve_path_within_cwd(cwd, op.path)
    if not resolved.is_file():
        raise ValueError(f"Cannot update missing file: {op.path}")

    file_lines = resolved.read_text(encoding="utf-8").splitlines()
    changes = 0
    diff_lines: list[str] = []

    for hunk in op.hunks:
        i = 0
        while i < len(hunk):
            prefix, content = hunk[i]
            if prefix == "-":
                old_line = content or ""
                new_line: str | None = None
                if i + 1 < len(hunk) and hunk[i + 1][0] == "+":
                    new_line = hunk[i + 1][1] or ""
                    i += 1
                replaced = False
                for idx, existing in enumerate(file_lines):
                    if existing == old_line:
                        diff_lines.append(f"-{old_line}")
                        if new_line is not None:
                            file_lines[idx] = new_line
                            diff_lines.append(f"+{new_line}")
                            changes += 1
                        else:
                            file_lines.pop(idx)
                            changes += 1
                        replaced = True
                        break
                if not replaced:
                    raise ValueError(
                        f"Could not find line to replace in {op.path}: {old_line!r}"
                    )
            elif prefix == "+":
                # standalone addition at end
                file_lines.append(content or "")
                diff_lines.append(f"+{content}")
                changes += 1
            i += 1

    resolved.write_text("\n".join(file_lines) + ("\n" if file_lines else ""), encoding="utf-8")
    return PatchResult(
        path=op.path,
        change_type="update",
        summary=f"updated {changes} line(s) in {op.path}",
        diff_snippet=_truncate("\n".join(diff_lines), max_snippet),
    )


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n[... truncated ...]"
