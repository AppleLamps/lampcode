from __future__ import annotations

import json
from pathlib import Path

MANIFEST_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "CMakeLists.txt",
)

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".agent-cli",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        "target",
        ".next",
        ".cache",
    }
)

_TEST_DIR_NAMES = ("tests", "test", "__tests__", "spec")
_TEST_FILE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"})


def detect_post_patch_test(cwd: Path) -> str:
    """Best-effort default for [harness] post_patch_test when scaffolding a project."""
    cwd = cwd.resolve()
    if _looks_like_python_project(cwd) and _has_test_tree(cwd):
        return "pytest -q"
    npm_test = _npm_test_command(cwd)
    if npm_test:
        return npm_test
    if (cwd / "Cargo.toml").is_file():
        return "cargo test"
    if (cwd / "go.mod").is_file():
        return "go test ./..."
    return ""


def _looks_like_python_project(cwd: Path) -> bool:
    if (cwd / "pyproject.toml").is_file() or (cwd / "pytest.ini").is_file():
        return True
    if (cwd / "conftest.py").is_file():
        return True
    return any(cwd.glob("*.py")) and _has_test_tree(cwd)


def _has_test_tree(cwd: Path) -> bool:
    for name in _TEST_DIR_NAMES:
        root = cwd / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in _TEST_FILE_SUFFIXES:
                return True
    return any(cwd.glob("test_*.py"))


def _npm_test_command(cwd: Path) -> str:
    pkg = cwd / "package.json"
    if not pkg.is_file():
        return ""
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict) or "test" not in scripts:
        return ""
    if (cwd / "pnpm-lock.yaml").is_file():
        return "pnpm test"
    if (cwd / "yarn.lock").is_file():
        return "yarn test"
    if (cwd / "bun.lockb").is_file() or (cwd / "bun.lock").is_file():
        return "bun test"
    return "npm test"


def _read_file_snippet(path: Path, max_bytes: int) -> str | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:
        text = raw[:max_bytes].decode("utf-8", errors="ignore") + "\n\n[... truncated ...]"
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.strip()


def _first_ci_workflow(cwd: Path) -> Path | None:
    workflows = cwd / ".github" / "workflows"
    if not workflows.is_dir():
        return None
    candidates = sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml"))
    return candidates[0] if candidates else None


def _tests_layout_hint(cwd: Path) -> str:
    lines: list[str] = []
    for name in _TEST_DIR_NAMES:
        root = cwd / name
        if not root.is_dir():
            continue
        count = sum(
            1
            for path in root.rglob("*")
            if path.is_file() and path.suffix in _TEST_FILE_SUFFIXES
        )
        if count:
            lines.append(f"- `{name}/` — about {count} test file(s)")
    if lines:
        return "## Test layout\n\n" + "\n".join(lines)
    return ""


def build_repo_map(
    cwd: Path,
    *,
    max_depth: int = 2,
    max_lines: int = 55,
) -> str:
    cwd = cwd.resolve()
    lines: list[str] = []

    def walk(base: Path, depth: int) -> None:
        if depth > max_depth or len(lines) >= max_lines:
            return
        try:
            entries = sorted(
                base.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return
        for entry in entries:
            if len(lines) >= max_lines:
                return
            if entry.name in _SKIP_DIRS:
                continue
            if entry.name.startswith(".") and entry.name not in (".github",):
                continue
            try:
                rel = entry.relative_to(cwd).as_posix()
            except ValueError:
                continue
            if entry.is_dir():
                lines.append(f"{rel}/")
                if depth < max_depth:
                    walk(entry, depth + 1)
            else:
                lines.append(rel)

    walk(cwd, 0)
    if not lines:
        return ""
    suffix = "\n[... tree truncated ...]" if len(lines) >= max_lines else ""
    body = "\n".join(f"- `{line}`" for line in lines)
    return f"## Repo map (depth ≤ {max_depth})\n\n{body}{suffix}"


def _trim_sections_to_budget(sections: list[str], max_bytes: int) -> str:
    joined = "\n\n".join(s for s in sections if s)
    encoded = joined.encode("utf-8")
    if len(encoded) <= max_bytes:
        return joined
    # Drop lowest-priority sections from the end until within budget.
    trimmed = list(sections)
    while len(trimmed) > 1:
        trimmed.pop()
        joined = "\n\n".join(s for s in trimmed if s)
        if len(joined.encode("utf-8")) <= max_bytes:
            return joined + "\n\n[... project context truncated ...]"
    text = trimmed[0] if trimmed else ""
    if len(text.encode("utf-8")) > max_bytes:
        return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore") + "\n\n[... truncated ...]"
    return text


def load_project_context(cwd: Path, max_bytes: int = 12288) -> str:
    """README, build manifests, CI snippet, test layout, and a shallow repo map."""
    cwd = cwd.resolve()
    sections: list[str] = []

    readme = _read_file_snippet(cwd / "README.md", max_bytes=4000)
    if readme:
        sections.append(f"## README.md\n\n{readme}")

    manifest_parts: list[str] = []
    per_manifest = 1800
    for name in MANIFEST_FILES:
        snippet = _read_file_snippet(cwd / name, max_bytes=per_manifest)
        if snippet:
            manifest_parts.append(f"### {name}\n\n```\n{snippet}\n```")
    if manifest_parts:
        sections.append("## Build / package manifests\n\n" + "\n\n".join(manifest_parts))

    ci_path = _first_ci_workflow(cwd)
    if ci_path:
        ci_snippet = _read_file_snippet(ci_path, max_bytes=1500)
        if ci_snippet:
            rel = ci_path.relative_to(cwd).as_posix()
            sections.append(f"## CI ({rel})\n\n```yaml\n{ci_snippet}\n```")

    tests_hint = _tests_layout_hint(cwd)
    if tests_hint:
        sections.append(tests_hint)

    repo_map = build_repo_map(cwd)
    if repo_map:
        sections.append(repo_map)

    skills_dir = cwd / ".agent-cli" / "skills"
    if skills_dir.is_dir():
        names = sorted(
            p.name
            for p in skills_dir.iterdir()
            if p.is_dir() and (p / "SKILL.md").is_file()
        )
        if names:
            sections.append(
                "## Project skills (use @mention or matching keywords)\n\n"
                + ", ".join(f"`{n}`" for n in names)
            )

    return _trim_sections_to_budget(sections, max_bytes)

