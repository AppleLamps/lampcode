from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.skills.discovery import discover_skills


@dataclass
class SkillIssue:
    level: str  # error | warning
    skill: str
    message: str


def diagnose_skills(cwd: Path, *, enable_project: bool = True, enable_user: bool = True) -> list[SkillIssue]:
    issues: list[SkillIssue] = []
    skills = discover_skills(cwd, enable_project=enable_project, enable_user=enable_user)
    names: dict[str, list[str]] = {}
    for skill in skills:
        names.setdefault(skill.name, []).append(str(skill.path))
        path = Path(skill.path)
        if not path.is_file():
            issues.append(SkillIssue("error", skill.name, "SKILL.md missing"))
            continue
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            issues.append(SkillIssue("warning", skill.name, "Missing YAML frontmatter"))
        if len(raw) > 8000:
            issues.append(SkillIssue("warning", skill.name, f"Body oversized ({len(raw)} chars)"))
        if not skill.description.strip():
            issues.append(SkillIssue("warning", skill.name, "Empty description in frontmatter"))
    for name, paths in names.items():
        if len(paths) > 1:
            issues.append(SkillIssue("warning", name, f"Duplicate skill name: {paths}"))
    return issues


def skills_doctor_report(cwd: Path) -> dict[str, Any]:
    issues = diagnose_skills(cwd)
    return {
        "ok": not any(i.level == "error" for i in issues),
        "issues": [{"level": i.level, "skill": i.skill, "message": i.message} for i in issues],
    }
