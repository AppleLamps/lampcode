from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.settings import MemoriesSettings, load_memories_settings


def _default_path(settings: MemoriesSettings) -> Path:
    return Path(settings.path).expanduser()


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


@dataclass
class Memory:
    id: str
    created_at: str
    source_thread_id: str | None
    text: str
    tags: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        return cls(
            id=str(data.get("id", "")),
            created_at=str(data.get("created_at", "")),
            source_thread_id=data.get("source_thread_id"),
            text=str(data.get("text", "")),
            tags=[str(t) for t in data.get("tags", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "source_thread_id": self.source_thread_id,
            "text": self.text,
            "tags": self.tags,
        }


class MemoryStore:
    def __init__(self, path: Path | None = None, *, settings: MemoriesSettings | None = None) -> None:
        self.settings = settings or MemoriesSettings()
        self.path = path or _default_path(self.settings)

    def list_all(self) -> list[Memory]:
        return [Memory.from_dict(x) for x in _load_store(self.path)]

    def add(self, text: str, *, source_thread_id: str | None = None, tags: list[str] | None = None) -> Memory:
        mem = Memory(
            id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            source_thread_id=source_thread_id,
            text=text.strip(),
            tags=tags or [],
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

    def search(self, query: str, *, limit: int = 10) -> list[Memory]:
        tokens = _tokenize(query)
        if not tokens:
            return self.list_all()[:limit]
        scored: list[tuple[int, Memory]] = []
        for mem in self.list_all():
            score = sum(1 for t in tokens if t in mem.text.lower())
            score += sum(2 for t in tokens if t in " ".join(mem.tags).lower())
            if score:
                scored.append((score, mem))
        scored.sort(key=lambda x: (-x[0], x[1].created_at), reverse=False)
        return [m for _, m in scored[:limit]]


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{3,}", text.lower()) if t]


def inject_memories_prompt(query: str, settings: MemoriesSettings | None = None) -> str:
    settings = settings or MemoriesSettings()
    if not settings.enabled:
        return ""
    store = MemoryStore(settings=settings)
    matches = store.search(query, limit=settings.max_inject)
    if not matches:
        return ""
    lines = ["# Relevant memories", ""]
    for mem in matches:
        tag_str = f" ({', '.join(mem.tags)})" if mem.tags else ""
        lines.append(f"- {mem.text}{tag_str}")
    return "\n".join(lines) + "\n"


def extract_memory_from_message(text: str, *, thread_id: str | None, store: MemoryStore | None = None) -> Memory | None:
    for line in text.splitlines():
        if line.strip().upper().startswith("MEMORY:"):
            body = line.split(":", 1)[1].strip()
            if body:
                s = store or MemoryStore()
                return s.add(body, source_thread_id=thread_id)
    return None
