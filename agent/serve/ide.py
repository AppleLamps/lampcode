from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from agent.models import Thread
from agent.paths import is_path_within_cwd, resolve_path_within_cwd
from agent.store import ThreadStore


class IdeError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def safe_resolve(cwd: Path, user_path: str) -> Path:
    """Resolve path within cwd; reject escapes and symlink targets outside cwd."""
    try:
        resolved = resolve_path_within_cwd(cwd, user_path)
    except ValueError as exc:
        raise IdeError(str(exc), status=403) from exc
    if resolved.is_symlink():
        target = resolved.resolve()
        if not is_path_within_cwd(cwd, target):
            raise IdeError("Symlink escapes working directory", status=403)
    return resolved


def list_tree(
    cwd: Path,
    rel_path: str = ".",
    *,
    max_depth: int = 5,
    max_entries: int = 2000,
) -> dict[str, Any]:
    base = safe_resolve(cwd, rel_path)
    if not base.is_dir():
        raise IdeError("Not a directory", status=404)
    count = 0

    def scan(dir_path: Path, depth: int, prefix: str) -> list[dict[str, Any]]:
        nonlocal count
        if depth > max_depth or count >= max_entries:
            return []
        result: list[dict[str, Any]] = []
        try:
            children = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            raise IdeError(f"Cannot read directory: {exc}", status=403) from exc
        for child in children:
            if count >= max_entries:
                break
            if child.name.startswith("."):
                continue
            if child.is_symlink():
                target = child.resolve()
                if not is_path_within_cwd(cwd, target):
                    continue
            rel = f"{prefix}/{child.name}" if prefix else child.name
            is_dir = child.is_dir() and not child.is_symlink()
            entry: dict[str, Any] = {
                "name": child.name,
                "path": rel.replace("\\", "/"),
                "type": "dir" if is_dir else "file",
            }
            count += 1
            if is_dir:
                entry["children"] = scan(child, depth + 1, rel)
            result.append(entry)
        return result

    prefix = "" if rel_path in (".", "") else rel_path.strip("./\\")
    entries = scan(base, 1, prefix)
    return {"path": rel_path, "entries": entries, "count": count}


def read_file(cwd: Path, rel_path: str, *, max_bytes: int) -> dict[str, Any]:
    path = safe_resolve(cwd, rel_path)
    if not path.is_file():
        raise IdeError("Not a file", status=404)
    if path.is_symlink():
        raise IdeError("Symlink files not allowed", status=403)
    size = path.stat().st_size
    if size > max_bytes:
        raise IdeError(f"File exceeds max size ({max_bytes} bytes)", status=413)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise IdeError("Binary file not supported", status=415) from exc
    except OSError as exc:
        raise IdeError(str(exc), status=500) from exc
    return {"path": rel_path, "content": content, "size": size}


def write_file_atomic(cwd: Path, rel_path: str, content: str, *, max_bytes: int) -> dict[str, Any]:
    if len(content.encode("utf-8")) > max_bytes:
        raise IdeError(f"Content exceeds max size ({max_bytes} bytes)", status=413)
    path = safe_resolve(cwd, rel_path)
    if path.exists() and path.is_symlink():
        raise IdeError("Cannot write through symlink", status=403)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".ide-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise IdeError(str(exc), status=500) from exc
    return {"path": rel_path, "bytes": len(content.encode("utf-8")), "ok": True}


def file_diff_from_thread(thread: Thread, rel_path: str) -> dict[str, Any]:
    """Return last fileChange diff snippet for path if tracked in thread turns."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    for turn in reversed(thread.turns):
        for item in reversed(turn.items):
            if item.type != "fileChange":
                continue
            item_path = (item.path or "").replace("\\", "/").lstrip("./")
            if item_path == norm or item_path.endswith("/" + norm):
                return {
                    "path": rel_path,
                    "change_type": item.change_type,
                    "status": item.status,
                    "summary": item.summary,
                    "diff_snippet": item.diff_snippet,
                }
    return {"path": rel_path, "diff_snippet": None}


def file_history_from_thread(
    thread: Thread,
    rel_path: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent fileChange summaries for a path (for IDE diff gutter)."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    history: list[dict[str, Any]] = []
    for turn in reversed(thread.turns):
        for item in reversed(turn.items):
            if item.type != "fileChange":
                continue
            item_path = (item.path or "").replace("\\", "/").lstrip("./")
            if item_path != norm and not item_path.endswith("/" + norm):
                continue
            history.append(
                {
                    "turn_id": turn.id,
                    "item_id": item.id,
                    "path": item.path,
                    "change_type": item.change_type,
                    "status": item.status,
                    "summary": item.summary,
                    "diff_snippet": item.diff_snippet,
                    "content_preview": (item.content or "")[:500] if item.content else None,
                }
            )
            if len(history) >= limit:
                return history
    return history


def compute_diff_gutter(
    current_content: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build added/removed line hints from last diff snippet vs current content."""
    if not history:
        return {"enabled": False, "added_lines": [], "removed_lines": []}
    snippet = history[0].get("diff_snippet") or history[0].get("content_preview") or ""
    if not snippet.strip():
        return {"enabled": False, "added_lines": [], "removed_lines": []}
    old_lines = snippet.splitlines()
    new_lines = current_content.splitlines()
    added, removed = _simple_line_diff(old_lines, new_lines)
    return {
        "enabled": True,
        "added_lines": added,
        "removed_lines": removed,
        "source_turn_id": history[0].get("turn_id"),
    }


def _simple_line_diff(old_lines: list[str], new_lines: list[str]) -> tuple[list[int], list[int]]:
    """Return 1-based line numbers added in new and removed from old (LCS heuristic)."""
    m, n = len(old_lines), len(new_lines)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            if old_lines[i] == new_lines[j]:
                dp[i][j] = 1 + dp[i + 1][j + 1]
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    added: list[int] = []
    removed: list[int] = []
    i, j = 0, 0
    while i < m and j < n:
        if old_lines[i] == new_lines[j]:
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            removed.append(i + 1)
            i += 1
        else:
            added.append(j + 1)
            j += 1
    while i < m:
        removed.append(i + 1)
        i += 1
    while j < n:
        added.append(j + 1)
        j += 1
    return added, removed


def enforce_tab_limit(open_tabs: list[str], *, max_tabs: int) -> tuple[list[str], bool]:
    if max_tabs <= 0:
        return open_tabs, False
    if len(open_tabs) <= max_tabs:
        return open_tabs, False
    return open_tabs[-max_tabs:], True


def tabs_state_snapshot(open_tabs: list[str] | None = None, active: str | None = None) -> dict[str, Any]:
    return {"open_tabs": open_tabs or [], "active": active}


def resolve_thread_cwd(store: ThreadStore, thread_id: str) -> tuple[Thread, Path]:
    try:
        thread = store.load_thread(thread_id)
    except FileNotFoundError:
        matches = [
            t for t in store.list_threads() if t.id.startswith(thread_id) or t.id == thread_id
        ]
        if len(matches) == 1:
            thread = matches[0]
        else:
            raise IdeError("Thread not found", status=404) from None
    cwd = Path(thread.cwd)
    if not cwd.is_dir():
        raise IdeError("Thread cwd does not exist", status=404)
    return thread, cwd
