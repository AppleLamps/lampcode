from __future__ import annotations

import subprocess
from pathlib import Path


def detect_repo_root(cwd: Path) -> str | None:
    """Return git repo root for cwd, or None if not in a repo / git unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        root = result.stdout.strip()
        return str(Path(root).resolve()) if root else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def is_inside_git_repo(cwd: Path) -> bool:
    return detect_repo_root(cwd) is not None
