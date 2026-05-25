from __future__ import annotations

from pathlib import Path


INIT_CONFIG_TEMPLATE = """profile = "interactive"
model_profile = "deep"
sandbox_mode = "workspace-write"
approval_mode = "interactive"

[project]
name = "{name}"
trust_git_root = true

[model_profiles.fast]
model = "google/gemini-2.5-flash-preview"
max_tool_rounds = 15

[model_profiles.deep]
model = "anthropic/claude-sonnet-4"
max_tool_rounds = 40
reasoning_effort = "high"
"""

AGENTS_MD_TEMPLATE = """# Project rules

- Run tests after code changes.
- Prefer small, focused commits with clear messages.
- Keep changes minimal and explain tradeoffs in commit messages.
"""

SKILL_TEMPLATE = """---
name: project-default
description: Default project skill scaffold
---

# Project skill

Add project-specific guidance here.
"""


def init_project(cwd: Path, *, name: str | None = None, yes: bool = False) -> dict[str, str]:
    cwd = cwd.resolve()
    project_name = name or cwd.name
    base = cwd / ".agent-cli"
    skills = base / "skills" / "project-default"
    created: dict[str, str] = {}

    if base.exists() and not yes:
        return {"skipped": "already exists — use --yes to overwrite scaffold files"}

    base.mkdir(parents=True, exist_ok=True)
    config_path = base / "config.toml"
    if not config_path.is_file() or yes:
        config_path.write_text(INIT_CONFIG_TEMPLATE.format(name=project_name), encoding="utf-8")
        created["config"] = str(config_path)

    agents_path = cwd / "AGENTS.md"
    if not agents_path.is_file() or yes:
        agents_path.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")
        created["agents"] = str(agents_path)

    skills.mkdir(parents=True, exist_ok=True)
    skill_md = skills / "SKILL.md"
    if not skill_md.is_file() or yes:
        skill_md.write_text(SKILL_TEMPLATE, encoding="utf-8")
        created["skill"] = str(skill_md)

    return created
