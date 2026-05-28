from __future__ import annotations

from pathlib import Path

from agent.project_context import detect_post_patch_test

INIT_CONFIG_TEMPLATE = """profile = "interactive"
model = "minimax/minimax-m2.7"
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
model = "minimax/minimax-m2.7"
max_tool_rounds = 40
reasoning_effort = "high"

[memories]
enabled = true
max_inject = 5
path = ".agent-cli/memories.json"

[web_search]
enabled = true
provider = "duckduckgo"

[harness]
{harness_lines}

# [action_log]
# enabled = true
# mirror_to_project = true   # also writes .agent-cli/action.log in the project

# [context]
# baseline_mode = "auto"   # auto | fixed | none
# headroom_tokens = 8000
# artifact_inline_limit = 8000

# [compaction]
# auto_mid_turn = true
# model = "google/gemini-2.5-flash-preview"
# pre_turn_threshold = 0.85
# tool_output_threshold = 0.75

# [budget]
# max_cost_usd_per_turn = 0.50

# [shell]
# enabled = false
# backend = "auto"  # auto | pipes | conpty | pty

# [tui]
# statusline = ["model", "cost", "mode", "sandbox", "approvals", "context", "branch", "session"]
# reduced_motion = true   # static working indicator (or set AGENT_TUI_REDUCED_MOTION=1)

# [mcp_servers.lsp]
# command = "agent"
# args = ["lsp-mcp"]
# enabled = true
# require_approval = false

# [.agent-cli/exec-policy.toml]
# [allow_prefixes]
# prefixes = ["pytest -q", "git status"]
"""

AGENTS_MD_TEMPLATE = """# Project rules

- Run tests after code changes. When `[harness] post_patch_test` is set, tests run automatically after successful `apply_patch` calls.
- Prefer `apply_patch` for edits; use `file_outline`, `go_to_definition`, `find_references`, and `file_imports` before refactors (plus `read_file` / `search_repo` as needed).
- Prefer small, focused commits with clear messages when using git.
- Keep changes minimal and explain tradeoffs in the final summary.
"""

SKILL_TEMPLATE = """---
name: project-default
description: Default project skill scaffold — testing, layout, and conventions for this repo
---

# Project skill

Document how to run tests, where main packages live, and any project-specific conventions.
"""


def _harness_config_lines(cwd: Path) -> str:
    cmd = detect_post_patch_test(cwd)
    if cmd:
        escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        return f'post_patch_test = "{escaped}"'
    return '# post_patch_test = "pytest -q"  # auto-detected when tests/ or package.json test script exists'


def _build_init_config(name: str, cwd: Path) -> str:
    return INIT_CONFIG_TEMPLATE.format(
        name=name,
        harness_lines=_harness_config_lines(cwd),
    )


def ensure_workspace_ready(cwd: Path) -> None:
    """Silently create project scaffold when missing (zero-setup like Codex)."""
    cwd = cwd.resolve()
    if not (cwd / ".agent-cli" / "config.toml").is_file():
        init_project(cwd, yes=False)


def init_project(cwd: Path, *, name: str | None = None, yes: bool = False) -> dict[str, str]:
    cwd = cwd.resolve()
    project_name = name or cwd.name
    base = cwd / ".agent-cli"
    skills = base / "skills" / "project-default"
    result: dict[str, str] = {}

    base.mkdir(parents=True, exist_ok=True)

    config_path = base / "config.toml"
    if not config_path.is_file() or yes:
        config_path.write_text(_build_init_config(project_name, cwd), encoding="utf-8")
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
