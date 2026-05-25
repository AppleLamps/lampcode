from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.settings import MemoriesSettings, load_memories_settings

INJECT_CHAR_CAP = 4000


def _default_path(settings: MemoriesSettings) -> Path:
    return Path(settings.path).expanduser()


def suggest_queue_path(cwd: Path) -> Path:
    return cwd.resolve() / ".agent-cli" / "memory-suggestions.json"


def _load_store(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_store(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def memories_store_path(settings: MemoriesSettings | None = None) -> Path:
    settings = settings or MemoriesSettings()
    return _default_path(settings)


@dataclass
class Memory:
    id: str
    created_at: str
    source_thread_id: str | None
    text: str
    tags: list[str]
    cwd: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        return cls(
            id=str(data.get("id", "")),
            created_at=str(data.get("created_at", "")),
            source_thread_id=data.get("source_thread_id"),
            text=str(data.get("text", "")),
            tags=[str(t) for t in data.get("tags", [])],
            cwd=data.get("cwd"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "source_thread_id": self.source_thread_id,
            "text": self.text,
            "tags": self.tags,
            "cwd": self.cwd,
        }


@dataclass
class ScoredMemory:
    memory: Memory
    score: float


class MemoryStore:
    def __init__(self, path: Path | None = None, *, settings: MemoriesSettings | None = None) -> None:
        self.settings = settings or MemoriesSettings()
        self.path = path or _default_path(self.settings)

    def list_all(self) -> list[Memory]:
        return [Memory.from_dict(x) for x in _load_store(self.path)]

    def add(
        self,
        text: str,
        *,
        source_thread_id: str | None = None,
        tags: list[str] | None = None,
        cwd: str | None = None,
    ) -> Memory:
        mem = Memory(
            id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            source_thread_id=source_thread_id,
            text=text.strip(),
            tags=tags or [],
            cwd=cwd,
        )
        items = _load_store(self.path)
        items.append(mem.to_dict())
        _save_store(self.path, items)
        return mem

    def delete(self, memory_id: str) -> bool:
        items = _load_store(self.path)
        new_items = [x for x in items if x.get("id") != memory_id]
        if len(new_items) == len(items):
            return False
        _save_store(self.path, new_items)
        return True

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        cwd: str | None = None,
    ) -> list[Memory]:
        scored = self.search_scored(query, cwd=cwd)
        return [entry.memory for entry in scored[:limit]]

    def search_scored(self, query: str, *, cwd: str | None = None) -> list[ScoredMemory]:
        tokens = _tokenize(query)
        if not tokens:
            return [ScoredMemory(memory=m, score=0.0) for m in self.list_all()]
        target_cwd = str(Path(cwd).resolve()) if cwd else None
        scored: list[ScoredMemory] = []
        for mem in self.list_all():
            score = _score_memory(mem, tokens, target_cwd=target_cwd)
            if score > 0:
                scored.append(ScoredMemory(memory=mem, score=score))
        scored.sort(key=lambda entry: (-entry.score, entry.memory.created_at), reverse=False)
        return scored


class SuggestQueue:
    def __init__(self, cwd: Path, *, max_pending: int = 20) -> None:
        self.path = suggest_queue_path(cwd)
        self.max_pending = max_pending

    def list_pending(self) -> list[Memory]:
        return [Memory.from_dict(x) for x in _load_store(self.path)]

    def pending_count(self) -> int:
        return len(_load_store(self.path))

    def enqueue(
        self,
        text: str,
        *,
        source_thread_id: str | None = None,
        cwd: str | None = None,
        tags: list[str] | None = None,
    ) -> Memory:
        mem = Memory(
            id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            source_thread_id=source_thread_id,
            text=text.strip(),
            tags=tags or [],
            cwd=cwd,
        )
        items = _load_store(self.path)
        items.append(mem.to_dict())
        while len(items) > self.max_pending:
            items.pop(0)
        _save_store(self.path, items)
        return mem

    def accept(self, memory_id: str, *, store: MemoryStore) -> Memory | None:
        items = _load_store(self.path)
        match = next((x for x in items if x.get("id") == memory_id), None)
        if match is None:
            return None
        items = [x for x in items if x.get("id") != memory_id]
        _save_store(self.path, items)
        return store.add(
            match["text"],
            source_thread_id=match.get("source_thread_id"),
            tags=match.get("tags") or [],
            cwd=match.get("cwd"),
        )

    def reject(self, memory_id: str) -> bool:
        items = _load_store(self.path)
        new_items = [x for x in items if x.get("id") != memory_id]
        if len(new_items) == len(items):
            return False
        _save_store(self.path, new_items)
        return True


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{3,}", text.lower()) if t]


def _recency_bonus(created_at: str) -> float:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)
    return max(0.0, 3.0 - age_days * 0.05)


def _score_memory(mem: Memory, tokens: list[str], *, target_cwd: str | None) -> float:
    text_lower = mem.text.lower()
    tag_blob = " ".join(mem.tags).lower()
    score = 0.0
    matched = 0
    for token in tokens:
        if token in text_lower:
            score += 1.5
            matched += 1
        if token in tag_blob:
            score += 3.0
            matched += 1
    if tokens:
        overlap = matched / len(tokens)
        score += overlap * 2.0
    score += _recency_bonus(mem.created_at)
    if target_cwd and mem.cwd:
        if str(Path(mem.cwd).resolve()) == target_cwd:
            score += 2.0
    return score


def inject_memories_prompt(
    query: str,
    settings: MemoriesSettings | None = None,
    *,
    cwd: str | None = None,
) -> str:
    settings = settings or MemoriesSettings()
    if not settings.enabled:
        return ""
    store = MemoryStore(settings=settings)
    matches = store.search(query, limit=settings.max_inject, cwd=cwd)
    return _format_inject_block(matches)


def inject_memories_dry_run(
    query: str,
    settings: MemoriesSettings | None = None,
    *,
    cwd: str | None = None,
) -> str:
    settings = settings or MemoriesSettings()
    if not settings.enabled:
        return "(memories disabled)"
    store = MemoryStore(settings=settings)
    scored = store.search_scored(query, cwd=cwd)[: settings.max_inject]
    matches = [entry.memory for entry in scored]
    block = _format_inject_block(matches)
    if len(block) > INJECT_CHAR_CAP:
        block = block[: INJECT_CHAR_CAP - 20] + "\n[... truncated ...]"
    return block or "(no matching memories)"


def _format_inject_block(matches: list[Memory]) -> str:
    if not matches:
        return ""
    lines = ["# Relevant memories", ""]
    for mem in matches:
        tag_str = f" ({', '.join(mem.tags)})" if mem.tags else ""
        lines.append(f"- {mem.text}{tag_str}")
    return "\n".join(lines) + "\n"


def extract_memory_from_message(
    text: str,
    *,
    thread_id: str | None,
    cwd: str | None = None,
    store: MemoryStore | None = None,
) -> Memory | None:
    for line in text.splitlines():
        if line.strip().upper().startswith("MEMORY:"):
            body = line.split(":", 1)[1].strip()
            if body:
                s = store or MemoryStore()
                return s.add(body, source_thread_id=thread_id, cwd=cwd)
    return None


def suggest_memory_from_turn(
    turn,
    *,
    thread_id: str,
    cwd: str,
    settings: MemoriesSettings,
    auto_approve: bool = False,
    remember_flag: bool = False,
) -> Memory | None:
    if not settings.enabled:
        return None
    if not remember_flag and not settings.auto_suggest and not (auto_approve and settings.suggest_on_auto_approve):
        return None
    agent_text = ""
    for item in reversed(turn.items):
        if item.type == "agentMessage":
            agent_text = item.text
            break
    if not agent_text.strip():
        return None
    if remember_flag or _looks_like_success_summary(agent_text):
        snippet = _first_meaningful_line(agent_text)
        if not snippet:
            return None
        if remember_flag:
            store = MemoryStore(settings=settings)
            return store.add(snippet, source_thread_id=thread_id, cwd=cwd)
        queue = SuggestQueue(Path(cwd), max_pending=settings.suggest_max_pending)
        return queue.enqueue(snippet, source_thread_id=thread_id, cwd=cwd)
    return None


def _looks_like_success_summary(text: str) -> bool:
    lower = text.lower()
    markers = ("pytest", "tests pass", "all tests", "fixed", "completed", "done")
    return any(marker in lower for marker in markers)


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:500]
    return text.strip()[:500]
