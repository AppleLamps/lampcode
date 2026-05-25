from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

from agent.paths import resolve_path_within_cwd
from tools.files import truncate_output


def search_repo(
    cwd: Path,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    max_results: int = 100,
    max_output: int = 20_000,
) -> str:
    search_root = resolve_path_within_cwd(cwd, path) if path else cwd

    if shutil.which("rg"):
        return _search_with_rg(search_root, pattern, glob, max_results, max_output)
    return _search_python(search_root, pattern, glob, max_results, max_output)


def _search_with_rg(
    root: Path,
    pattern: str,
    glob: str | None,
    max_results: int,
    max_output: int,
) -> str:
    cmd = ["rg", "-n", "--no-heading", "--color=never", pattern, str(root)]
    if glob:
        cmd.extend(["--glob", glob])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"Search error: {exc}"

    lines = proc.stdout.splitlines()[:max_results]
    if not lines:
        return "No matches found."
    result = "\n".join(lines)
    if len(lines) == max_results:
        result += f"\n\n[... truncated to {max_results} matches ...]"
    return truncate_output(result, max_output)


def _search_python(
    root: Path,
    pattern: str,
    glob_pattern: str | None,
    max_results: int,
    max_output: int,
) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Invalid regex pattern: {exc}"

    matches: list[str] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if glob_pattern and not fnmatch.fnmatch(file_path.name, glob_pattern):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = file_path.relative_to(root)
                matches.append(f"{rel}:{line_no}:{line}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    if not matches:
        return "No matches found."
    result = "\n".join(matches)
    if len(matches) == max_results:
        result += f"\n\n[... truncated to {max_results} matches ...]"
    return truncate_output(result, max_output)
