from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


def sync_state_dir() -> Path:
    base = Path.home() / ".agent-cli" / "sync-state"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sync_state_path(thread_id: str) -> Path:
    return sync_state_dir() / f"{thread_id}.json"


@dataclass
class FileSyncState:
    path: str
    local_mtime: float | None = None
    local_size: int | None = None
    local_hash: str | None = None
    remote_mtime: float | None = None
    remote_size: int | None = None
    remote_hash: str | None = None
    last_synced_at: str | None = None
    last_direction: str | None = None


@dataclass
class ThreadSyncState:
    thread_id: str
    files: dict[str, FileSyncState] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "files": {k: asdict(v) for k, v in self.files.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> ThreadSyncState:
        files = {}
        for path, raw in data.get("files", {}).items():
            files[path] = FileSyncState(path=path, **{k: v for k, v in raw.items() if k != "path"})
        return cls(thread_id=data.get("thread_id", ""), files=files)


class SyncStateStore:
    def load(self, thread_id: str) -> ThreadSyncState:
        path = sync_state_path(thread_id)
        if not path.is_file():
            return ThreadSyncState(thread_id=thread_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return ThreadSyncState.from_dict(data)

    def save(self, state: ThreadSyncState) -> Path:
        path = sync_state_path(state.thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        return path
