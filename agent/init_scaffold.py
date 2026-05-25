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

# [memories]
# enabled = false
# max_inject = 5

# [budget]
# max_cost_usd_per_turn = 0.50

# [harness]
# post_patch_test = "pytest -q"
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
    result: dict[str, str] = {}

    base.mkdir(parents=True, exist_ok=True)

    config_path = base / "config.toml"
    if not config_path.is_file() or yes:
        config_path.write_text(INIT_CONFIG_TEMPLATE.format(name=project_name), encoding="utf-8")
        result["config"] = str(config_path)
    elif config_path.is_file():
        result["skipped_config"] = "config.toml exists — use --yes to overwrite"

    agents_path = cwd / "AGENTS.md"
    if not agents_path.is_file() or yes:
        agents_path.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")
        result["agents"] = str(agents_path)
    elif agents_path.is_file():
        result["skipped_agents"] = "AGENTS.md exists — use --yes to overwrite"

    skills.mkdir(parents=True, exist_ok=True)
    skill_md = skills / "SKILL.md"
    if not skill_md.is_file() or yes:
        skill_md.write_text(SKILL_TEMPLATE, encoding="utf-8")
        result["skill"] = str(skill_md)
    elif skill_md.is_file():
        result["skipped_skill"] = "skills/project-default exists — use --yes to overwrite"

    if not any(k in result for k in ("config", "agents", "skill")) and result:
        result.setdefault("message", "scaffold already present — no files changed")

    return result
