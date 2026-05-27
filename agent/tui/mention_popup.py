"""@file and @skill mention suggestions for the composer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent.settings import load_skills_config
from agent.skills.discovery import discover_skills

_MENTION_RE = re.compile(r"@([\w./\\-]*)$")


@dataclass
class MentionCandidate:
    label: str
    insert: str
    kind: str  # file | skill


@dataclass
class MentionBinding:
    """Stable link between visible @mention text and resolved target."""

    mention: str
    target: str
    kind: str  # file | skill


_MENTION_TOKEN_RE = re.compile(r"@([\w./\\-]+)")


def binding_from_candidate(candidate: MentionCandidate) -> MentionBinding:
    token = candidate.insert.strip().lstrip("@").strip()
    return MentionBinding(
        mention=candidate.insert.strip(),
        target=token,
        kind=candidate.kind,
    )


def rebuild_bindings_from_text(text: str, cwd: Path) -> list[MentionBinding]:
    """Reconstruct bindings when reloading saved composer text."""
    bindings: list[MentionBinding] = []
    seen: set[str] = set()
    for match in _MENTION_TOKEN_RE.finditer(text):
        token = match.group(1).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        mention = f"@{token}"
        kind = "skill"
        target = token
        if "/" in token or "\\" in token or "." in token:
            kind = "file"
            if (cwd / token).is_file():
                target = token
            else:
                for cand in _file_candidates(cwd, token, limit=1):
                    target = cand.insert.strip().lstrip("@").strip()
                    break
        else:
            for cand in _skill_candidates(cwd, token, limit=1):
                if cand.insert.strip().lstrip("@").startswith(token):
                    target = cand.insert.strip().lstrip("@").strip()
                    kind = "skill"
                    break
        bindings.append(MentionBinding(mention=mention, target=target, kind=kind))
    return bindings


def active_mention_suffix(text: str) -> str | None:
    """Return the partial path/name after the last @ if the cursor is in a mention."""
    match = _MENTION_RE.search(text)
    if not match:
        return None
    return match.group(1)


def _file_candidates(cwd: Path, partial: str, *, limit: int = 12) -> list[MentionCandidate]:
    partial = partial.strip()
    results: list[MentionCandidate] = []
    if partial and (cwd / partial).is_file():
        results.append(MentionCandidate(label=partial, insert=f"@{partial} ", kind="file"))

    try:
        entries = list(cwd.iterdir())
    except OSError:
        return results

    for path in sorted(entries, key=lambda p: p.name.lower()):
        if path.name.startswith("."):
            continue
        rel = path.name
        if path.is_dir():
            rel = rel + "/"
        if partial and not (rel.startswith(partial) or partial in rel):
            continue
        label = rel + (" (dir)" if path.is_dir() else "")
        results.append(MentionCandidate(label=label, insert=f"@{rel} ", kind="file"))
        if len(results) >= limit:
            break
    return results


def _skill_candidates(
    cwd: Path,
    partial: str,
    *,
    limit: int = 8,
) -> list[MentionCandidate]:
    cfg = load_skills_config()
    skills = discover_skills(
        cwd,
        enable_project=cfg.enable_project_skills,
        enable_user=cfg.enable_user_skills,
    )
    partial_lower = partial.lower()
    out: list[MentionCandidate] = []
    for skill in skills:
        if partial_lower and not (
            skill.name.lower().startswith(partial_lower)
            or partial_lower in skill.name.lower()
        ):
            continue
        desc = skill.description[:60] + ("…" if len(skill.description) > 60 else "")
        label = f"{skill.name} — {desc}"
        out.append(
            MentionCandidate(
                label=label,
                insert=f"@{skill.name} ",
                kind="skill",
            )
        )
        if len(out) >= limit:
            break
    return out


def list_mention_candidates(text: str, cwd: Path) -> list[MentionCandidate]:
    partial = active_mention_suffix(text)
    if partial is None:
        return []
    files = _file_candidates(cwd, partial)
    skills = _skill_candidates(cwd, partial)
    if "/" in partial or "\\" in partial:
        return files[:12]
    return (skills[:6] + files[:6])[:12]


def apply_mention(text: str, candidate: MentionCandidate) -> str:
    """Replace the trailing @partial with the chosen mention insert text."""
    match = _MENTION_RE.search(text)
    if not match:
        return text + candidate.insert
    return text[: match.start()] + candidate.insert
