from __future__ import annotations

from agent.skills.discovery import Skill


def build_skills_prompt(skills: list[Skill], *, max_body_chars: int = 4000) -> str:
    if not skills:
        return ""

    parts = ["# Active Skills"]
    for skill in skills:
        body = skill.body
        if len(body) > max_body_chars:
            body = body[: max_body_chars - 20] + "\n[... truncated ...]"
        parts.append(f"## {skill.name}\n{body}")
    return "\n\n".join(parts) + "\n"
