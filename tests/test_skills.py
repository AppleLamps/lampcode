from pathlib import Path

from agent.skills.discovery import discover_skills, parse_skill_md
from agent.skills.injector import build_skills_prompt
from agent.skills.selector import extract_skill_mentions, select_skills


SKILL_WITH_FM = """---
name: custom-name
description: Helps with pytest failures.
---

# Body

Run pytest first.
"""

SKILL_NO_FM = """# Inferred Skill

Use this when testing things.
"""


def test_parse_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text(SKILL_WITH_FM, encoding="utf-8")
    skill = parse_skill_md(path, "project")
    assert skill.name == "custom-name"
    assert "pytest" in skill.description
    assert "Run pytest" in skill.body


def test_parse_without_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "inferred-skill"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text(SKILL_NO_FM, encoding="utf-8")
    skill = parse_skill_md(path, "user")
    assert skill.name == "inferred-skill"
    assert "testing" in skill.description.lower()


def test_discover_project_skills(tmp_path: Path) -> None:
    base = tmp_path / ".agent-cli" / "skills" / "pytest-fix"
    base.mkdir(parents=True)
    (base / "SKILL.md").write_text(
        "---\nname: pytest-fix\ndescription: pytest helper\n---\n\nBody",
        encoding="utf-8",
    )
    skills = discover_skills(tmp_path)
    assert any(s.name == "pytest-fix" for s in skills)


def test_extract_skill_mentions() -> None:
    assert "pytest-fix" in extract_skill_mentions("Use @pytest-fix please")


def test_select_by_keyword(tmp_path: Path) -> None:
    base = tmp_path / "skills" / "pytest-fix"
    base.mkdir(parents=True)
    (base / "SKILL.md").write_text(
        "---\nname: pytest-fix\ndescription: pytest testing helper\n---\n\nx",
        encoding="utf-8",
    )
    skills = discover_skills(tmp_path)
    selected = select_skills(skills, "fix pytest tests", max_active=3)
    assert selected[0].name == "pytest-fix"


def test_select_cap_at_max(tmp_path: Path) -> None:
    for i in range(5):
        d = tmp_path / "skills" / f"skill-{i}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: skill-{i}\ndescription: keyword shared\n---\n\nbody",
            encoding="utf-8",
        )
    skills = discover_skills(tmp_path)
    selected = select_skills(skills, "keyword shared", max_active=3)
    assert len(selected) == 3


def test_injector_truncates() -> None:
    from agent.skills.discovery import Skill

    skill = Skill(name="big", description="d", body="x" * 5000, path=Path("."), source="project")
    text = build_skills_prompt([skill], max_body_chars=100)
    assert "truncated" in text
