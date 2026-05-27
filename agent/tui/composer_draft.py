"""Per-thread composer draft persistence with mention bindings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent.tui.mention_popup import MentionBinding, rebuild_bindings_from_text


@dataclass
class ComposerDraft:
    text: str = ""
    bindings: list[MentionBinding] = field(default_factory=list)


def draft_path(cwd: Path, thread_id: str) -> Path:
    safe = thread_id.replace("/", "_").replace("\\", "_")
    return cwd.resolve() / ".agent-cli" / "composer-drafts" / f"{safe}.json"


def load_composer_draft(cwd: Path, thread_id: str) -> ComposerDraft:
    path = draft_path(cwd, thread_id)
    if not path.is_file():
        return ComposerDraft()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ComposerDraft()
    if not isinstance(data, dict):
        return ComposerDraft()
    text = str(data.get("text", ""))
    bindings_raw = data.get("bindings") or []
    bindings: list[MentionBinding] = []
    if isinstance(bindings_raw, list):
        for item in bindings_raw:
            if not isinstance(item, dict):
                continue
            mention = str(item.get("mention", "")).strip()
            target = str(item.get("target", "")).strip()
            kind = str(item.get("kind", "file"))
            if mention and target:
                bindings.append(MentionBinding(mention=mention, target=target, kind=kind))
    if text and not bindings:
        bindings = rebuild_bindings_from_text(text, cwd)
    return ComposerDraft(text=text, bindings=bindings)


def save_composer_draft(cwd: Path, thread_id: str, draft: ComposerDraft) -> None:
    path = draft_path(cwd, thread_id)
    if not draft.text.strip():
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text": draft.text,
        "bindings": [asdict(b) for b in draft.bindings],
    }
    path.write_text(json.dumps(payload, indent=0), encoding="utf-8")


def clear_composer_draft(cwd: Path, thread_id: str) -> None:
    path = draft_path(cwd, thread_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
