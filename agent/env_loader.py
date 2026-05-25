from __future__ import annotations

import os
from pathlib import Path


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, _, raw = stripped.partition("=")
    key = key.strip()
    if not key:
        return None
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_env_file(path: Path, *, override: bool = False) -> int:
    """Load KEY=VALUE pairs from a .env file into os.environ."""
    if not path.is_file():
        return 0
    loaded = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        parsed = _parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_dotenv_files(*, cwd: Path | None = None) -> list[Path]:
    """Load .env from cwd, git root, and user config dir (first wins for each key)."""
    loaded_paths: list[Path] = []
    start = (cwd or Path.cwd()).resolve()

    candidates: list[Path] = []
    candidates.append(Path.home() / ".agent-cli" / ".env")
    candidates.append(start / ".env")

    from agent.git import detect_repo_root

    repo_root = detect_repo_root(start)
    if repo_root:
        candidates.append(Path(repo_root) / ".env")

    package_root = Path(__file__).resolve().parent.parent
    candidates.append(package_root / ".env")

    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if load_env_file(resolved):
            loaded_paths.append(resolved)
    return loaded_paths
