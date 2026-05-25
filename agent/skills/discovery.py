from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    source: str  # project | user


def parse_skill_md(path: Path, source: str) -> Skill:
    text = path.read_text(encoding="utf-8", errors="replace")
    folder_name = path.parent.name
    name = folder_name
    description = ""
    body = text

    match = _FRONTMATTER_RE.match(text)
    if match:
        frontmatter = match.group(1)
        body = text[match.end() :]
        for line in frontmatter.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key == "name":
                    name = val
                elif key == "description":
                    description = val

    if not description:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line
                break

    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        path=path,
        source=source,
    )


def discover_skills(
    cwd: Path,
    *,
    enable_project: bool = True,
    enable_user: bool = True,
) -> list[Skill]:
    skills: dict[str, Skill] = {}
    search_paths: list[tuple[Path, str]] = []

    if enable_project:
        search_paths.extend(
            [
                (cwd / ".agent-cli" / "skills", "project"),
                (cwd / "skills", "project"),
            ]
        )
    if enable_user:
        search_paths.append((Path.home() / ".agent-cli" / "skills", "user"))

    for base, source in search_paths:
        if not base.is_dir():
            continue
        for skill_dir in sorted(base.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                skill = parse_skill_md(skill_md, source)
                # project skills override user skills on name collision
                if skill.name not in skills or source == "project":
                    skills[skill.name] = skill

    return list(skills.values())
