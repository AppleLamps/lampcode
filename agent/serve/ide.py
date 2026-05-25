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
