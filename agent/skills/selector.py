from __future__ import annotations

import re

from agent.skills.discovery import Skill

_MENTION_RE = re.compile(r"@([a-zA-Z0-9_-]+)")


def extract_skill_mentions(prompt: str) -> set[str]:
    return set(_MENTION_RE.findall(prompt))


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zA-Z0-9_-]+", text) if len(w) >= 3}


def select_skills(
    skills: list[Skill],
    prompt: str,
    *,
    max_active: int = 3,
) -> list[Skill]:
    if not skills:
        return []

    mentions = extract_skill_mentions(prompt)
    prompt_tokens = _tokenize(prompt)
    scored: list[tuple[int, Skill]] = []

    for skill in skills:
        if skill.name in mentions:
            scored.append((1000, skill))
            continue

        skill_tokens = _tokenize(skill.name + " " + skill.description)
        overlap = prompt_tokens & skill_tokens
        if overlap:
            scored.append((len(overlap), skill))

    scored.sort(key=lambda x: (-x[0], x[1].name))
    selected: list[Skill] = []
    seen: set[str] = set()
    for _, skill in scored:
        if skill.name in seen:
            continue
        selected.append(skill)
        seen.add(skill.name)
        if len(selected) >= max_active:
            break

    # Explicit mentions always included even if over cap (trim only keyword matches)
    for name in mentions:
        if any(s.name == name for s in selected):
            continue
        match = next((s for s in skills if s.name == name), None)
        if match:
            selected.insert(0, match)

    return selected[:max_active]
