from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.multi_agent.registry import WorkerRegistry


def default_dag_state_dir() -> Path:
    return Path.home() / ".agent-cli" / "dag-state"


@dataclass
class PersistedDagState:
    thread_id: str
    last_turn_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    dag_status: str = "running"
    spawn_count: int = 0
    pending_queue: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersistedDagState:
        return cls(
            thread_id=str(data["thread_id"]),
            last_turn_id=str(data.get("last_turn_id", "")),
            nodes=list(data.get("nodes", [])),
            edges=list(data.get("edges", [])),
            dag_status=str(data.get("dag_status", "running")),
            spawn_count=int(data.get("spawn_count", 0)),
            pending_queue=list(data.get("pending_queue", [])),
            updated_at=float(data.get("updated_at", time.time())),
        )


class DagStateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or default_dag_state_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, thread_id: str) -> Path:
        return self.base_dir / f"{thread_id}.json"

    def save(self, state: PersistedDagState, *, touch: bool = True) -> Path:
        if touch:
            state.updated_at = time.time()
        path = self.path_for(state.thread_id)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, thread_id: str) -> PersistedDagState | None:
        path = self.path_for(thread_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return PersistedDagState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def clear(self, thread_id: str) -> bool:
        path = self.path_for(thread_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def is_expired(self, state: PersistedDagState, max_age_sec: int) -> bool:
        if max_age_sec <= 0:
            return False
        return (time.time() - state.updated_at) > max_age_sec

    def save_from_registry(
        self,
        registry: WorkerRegistry,
        *,
        thread_id: str,
        turn_id: str,
    ) -> Path | None:
        snap = registry.build_graph_snapshot()
        with registry._lock:
            pending = list(registry._pending_queue)
            spawn_count = registry.spawn_count
            dag_status = registry._dag_status
        state = PersistedDagState(
            thread_id=thread_id,
            last_turn_id=turn_id,
            nodes=snap.nodes,
            edges=snap.edges,
            dag_status=dag_status,
            spawn_count=spawn_count,
            pending_queue=pending,
        )
        return self.save(state)


def clear_dag_state_for_thread(thread_id: str, base_dir: Path | None = None) -> None:
    DagStateStore(base_dir).clear(thread_id)
