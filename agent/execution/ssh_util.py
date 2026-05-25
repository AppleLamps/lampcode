from __future__ import annotations

from pathlib import Path


def expand_ssh_path(path: str) -> str:
    if path.startswith("~"):
        return str(Path(path).expanduser())
    return path
