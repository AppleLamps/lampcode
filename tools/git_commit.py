from __future__ import annotations

import shlex
import subprocess
from typing import Any


def git_commit(message: str, *, all_files: bool = False, cwd: str = ".") -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if status.returncode != 0:
        return f"git status failed: {status.stderr or status.stdout}"
    if not status.stdout.strip():
        return "Nothing to commit (working tree clean)"

    if all_files:
        add = subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True, timeout=30)
        if add.returncode != 0:
            return f"git add failed: {add.stderr or add.stdout}"

    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if commit.returncode != 0:
        return f"git commit failed: {commit.stderr or commit.stdout}"
    return (commit.stdout or "Committed.").strip()


GIT_COMMIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_commit",
        "description": (
            "Create a local git commit with a clear message. Never pushes to remote. "
            "Use after completing a logical unit of work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
                "all": {"type": "boolean", "description": "Stage all changes (git add -A) before commit"},
            },
            "required": ["message"],
        },
    },
}
