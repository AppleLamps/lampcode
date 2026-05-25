from __future__ import annotations

from pathlib import Path


def default_config_path() -> Path:
    return Path.home() / ".agent-cli" / "config.toml"


def default_store_dir() -> Path:
    return Path.home() / ".agent-cli" / "threads"


def resolve_path_within_cwd(cwd: Path, user_path: str) -> Path:
    """Resolve user_path relative to cwd and reject escapes outside cwd."""
    cwd = cwd.resolve()
    candidate = (cwd / user_path).resolve() if not Path(user_path).is_absolute() else Path(user_path).resolve()

    try:
        candidate.relative_to(cwd)
    except ValueError as exc:
        raise ValueError(
            f"Path escapes working directory: {user_path!r} resolves to {candidate}"
        ) from exc

    return candidate


def is_path_within_cwd(cwd: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(cwd.resolve())
        return True
    except ValueError:
        return False
