from __future__ import annotations

from pathlib import Path

from agent.paths import resolve_path_within_cwd


def enforce_workdir(cwd: Path, workdir: str | None = None) -> Path:
    """Resolve and lock subprocess cwd under thread cwd."""
    base = cwd.resolve()
    if workdir:
        return resolve_path_within_cwd(base, workdir)
    return base
