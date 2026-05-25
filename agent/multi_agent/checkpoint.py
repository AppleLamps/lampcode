from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.multi_agent.registry import WorkerRecord, WorkerRegistry


def default_checkpoint_dir() -> Path:
    return Path.home() / ".agent-cli" / "checkpoints"


def checkpoint_path(thread_id: str, turn_id: str, base: Path | None = None) -> Path:
    root = base or default_checkpoint_dir()
    return root / thread_id / f"{turn_id}.json"


@dataclass
class WorkerCheckpoint:
    worker_id: str
    parent_thread_id: str
    task: str
    depth: int
    status: str = "queued"
    worker_thread_id: str = ""
    summary: str | None = None
    execution_backend: str | None = None
    title: str | None = None
    model: str | None = None
    item_id: str = ""
    error: str | None = None


@dataclass
class SupervisorCheckpoint:
    thread_id: str
    turn_id: str
    spawn_count: int = 0
    workers: list[WorkerCheckpoint] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"
    user_text: str = ""

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "spawn_count": self.spawn_count,
            "workers": [asdict(w) for w in self.workers],
            "messages": self.messages,
            "status": self.status,
            "user_text": self.user_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SupervisorCheckpoint:
        workers = [WorkerCheckpoint(**w) for w in data.get("workers", [])]
        return cls(
            thread_id=data["thread_id"],
            turn_id=data["turn_id"],
            spawn_count=int(data.get("spawn_count", 0)),
            workers=workers,
            messages=list(data.get("messages", [])),
            status=data.get("status", "running"),
            user_text=data.get("user_text", ""),
        )


class CheckpointStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or default_checkpoint_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: SupervisorCheckpoint) -> Path:
        path = checkpoint_path(checkpoint.thread_id, checkpoint.turn_id, self.base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(checkpoint.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, thread_id: str, turn_id: str) -> SupervisorCheckpoint | None:
        path = checkpoint_path(thread_id, turn_id, self.base_dir)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return SupervisorCheckpoint.from_dict(data)

    def find_latest(self, thread_id: str) -> SupervisorCheckpoint | None:
        dir_path = self.base_dir / thread_id
        if not dir_path.is_dir():
            return None
        files = sorted(dir_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                return SupervisorCheckpoint.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue
        return None

    def list_for_thread(self, thread_id: str) -> list[SupervisorCheckpoint]:
        dir_path = self.base_dir / thread_id
        if not dir_path.is_dir():
            return []
        out: list[SupervisorCheckpoint] = []
        for f in sorted(dir_path.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append(SupervisorCheckpoint.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return out


def snapshot_registry(
    registry: WorkerRegistry,
    *,
    thread_id: str,
    turn_id: str,
    spawn_count: int,
    messages: list[dict] | None = None,
    status: str = "running",
    user_text: str = "",
) -> SupervisorCheckpoint:
    workers: list[WorkerCheckpoint] = []
    with registry._lock:
        for rec in registry._workers.values():
            workers.append(
                WorkerCheckpoint(
                    worker_id=rec.worker_id,
                    parent_thread_id=rec.parent_thread_id,
                    task=rec.task,
                    depth=rec.depth,
                    status=rec.status,
                    worker_thread_id=rec.worker_thread_id,
                    summary=rec.summary,
                    execution_backend=rec.execution_backend,
                    title=rec.title,
                    model=rec.model,
                    item_id=rec.item_id,
                    error=rec.error,
                )
            )
    return SupervisorCheckpoint(
        thread_id=thread_id,
        turn_id=turn_id,
        spawn_count=spawn_count,
        workers=workers,
        messages=list(messages or []),
        status=status,
        user_text=user_text,
    )


def restore_registry(
    config: Config,
    store,
    checkpoint: SupervisorCheckpoint,
    *,
    emitter=None,
    parent_thread=None,
    retry_failed: bool = False,
) -> WorkerRegistry:
    registry = WorkerRegistry(
        config,
        store,
        emitter=emitter,
        parent_thread=parent_thread,
        turn_id=checkpoint.turn_id,
    )
    registry._spawn_count_restore = checkpoint.spawn_count
    for wc in checkpoint.workers:
        if wc.status == "completed" and not retry_failed:
            record = WorkerRecord(
                worker_id=wc.worker_id,
                parent_thread_id=wc.parent_thread_id,
                task=wc.task,
                depth=wc.depth,
                status="completed",
                worker_thread_id=wc.worker_thread_id,
                summary=wc.summary,
                execution_backend=wc.execution_backend,
                title=wc.title,
                model=wc.model,
                item_id=wc.item_id,
                error=wc.error,
            )
            record._done.set()
            registry._workers[wc.worker_id] = record
        elif wc.status == "failed" and retry_failed:
            record = WorkerRecord(
                worker_id=wc.worker_id,
                parent_thread_id=wc.parent_thread_id,
                task=wc.task,
                depth=wc.depth,
                status="queued",
                worker_thread_id=wc.worker_thread_id,
                summary=wc.summary,
                execution_backend=wc.execution_backend,
                title=wc.title,
                model=wc.model,
                item_id=wc.item_id,
                error=wc.error,
            )
            registry._workers[wc.worker_id] = record
            registry._pending_queue.append(wc.worker_id)
        elif wc.status in ("queued", "running", "timed_out"):
            record = WorkerRecord(
                worker_id=wc.worker_id,
                parent_thread_id=wc.parent_thread_id,
                task=wc.task,
                depth=wc.depth,
                status="queued",
                worker_thread_id=wc.worker_thread_id,
                summary=wc.summary,
                execution_backend=wc.execution_backend,
                title=wc.title,
                model=wc.model,
                item_id=wc.item_id,
                error=wc.error,
            )
            registry._workers[wc.worker_id] = record
            registry._pending_queue.append(wc.worker_id)
    return registry


def save_checkpoint_from_registry(
    registry: WorkerRegistry,
    config: Config,
    *,
    thread_id: str,
    turn_id: str,
    spawn_count: int,
    messages: list | None = None,
    status: str = "running",
    user_text: str = "",
) -> Path | None:
    if not config.multi_agent.checkpoint_enabled:
        return None
    base = Path(config.multi_agent.checkpoint_dir).expanduser()
    cp = snapshot_registry(
        registry,
        thread_id=thread_id,
        turn_id=turn_id,
        spawn_count=spawn_count,
        messages=messages,
        status=status,
        user_text=user_text,
    )
    return CheckpointStore(base).save(cp)
