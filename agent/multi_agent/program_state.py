from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_programs_dir() -> Path:
    return Path.home() / ".agent-cli" / "programs"


@dataclass
class ProgramNode:
    worker_id: str
    thread_id: str
    turn_id: str = ""
    task: str = ""
    status: str = "queued"
    parent_thread_id: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgramNode:
        return cls(
            worker_id=str(data.get("worker_id", "")),
            thread_id=str(data.get("thread_id", "")),
            turn_id=str(data.get("turn_id", "")),
            task=str(data.get("task", "")),
            status=str(data.get("status", "queued")),
            parent_thread_id=str(data.get("parent_thread_id", "")),
            updated_at=float(data.get("updated_at", time.time())),
        )


@dataclass
class ProgramState:
    program_id: str
    linked_thread_ids: list[str] = field(default_factory=list)
    nodes: list[ProgramNode] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    dag_status: str = "running"
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "linked_thread_ids": self.linked_thread_ids,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": self.edges,
            "dag_status": self.dag_status,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgramState:
        return cls(
            program_id=str(data.get("program_id", "")),
            linked_thread_ids=[str(x) for x in data.get("linked_thread_ids", [])],
            nodes=[ProgramNode.from_dict(n) for n in data.get("nodes", [])],
            edges=[dict(e) for e in data.get("edges", [])],
            dag_status=str(data.get("dag_status", "running")),
            updated_at=float(data.get("updated_at", time.time())),
        )


def derive_program_id(cwd: Path, *, auto: bool = True) -> str:
    if not auto:
        return hashlib.sha256(str(cwd.resolve()).encode()).hexdigest()[:16]
    remote = ""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            remote = proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    key = f"{remote}|{cwd.resolve()}".lower()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class ProgramStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or default_programs_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, program_id: str) -> Path:
        return self.base_dir / f"{program_id}.json"

    def load(self, program_id: str) -> ProgramState | None:
        path = self.path_for(program_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ProgramState.from_dict(data)
        except (json.JSONDecodeError, TypeError):
            return None

    def save(self, state: ProgramState) -> Path:
        state.updated_at = time.time()
        path = self.path_for(state.program_id)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        return path

    def list_programs(self) -> list[str]:
        return sorted(p.stem for p in self.base_dir.glob("*.json"))

    def clear(self, program_id: str) -> bool:
        path = self.path_for(program_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def link_thread(self, program_id: str, thread_id: str, *, max_threads: int = 20) -> ProgramState:
        state = self.load(program_id) or ProgramState(program_id=program_id)
        if thread_id not in state.linked_thread_ids:
            if max_threads > 0 and len(state.linked_thread_ids) >= max_threads:
                raise ValueError(f"max_threads_linked ({max_threads}) reached")
            state.linked_thread_ids.append(thread_id)
        self.save(state)
        return state

    def upsert_node(
        self,
        program_id: str,
        node: ProgramNode,
        *,
        edges: list[dict[str, str]] | None = None,
        thread_id: str | None = None,
        max_threads: int = 20,
    ) -> ProgramState:
        if thread_id:
            self.link_thread(program_id, thread_id, max_threads=max_threads)
        state = self.load(program_id) or ProgramState(program_id=program_id)
        replaced = False
        for i, existing in enumerate(state.nodes):
            if existing.worker_id == node.worker_id:
                state.nodes[i] = node
                replaced = True
                break
        if not replaced:
            state.nodes.append(node)
        if edges:
            for edge in edges:
                if edge not in state.edges:
                    state.edges.append(edge)
        self.save(state)
        return state

    def get_node(self, program_id: str, worker_id: str) -> ProgramNode | None:
        state = self.load(program_id)
        if not state:
            return None
        for node in state.nodes:
            if node.worker_id == worker_id:
                return node
        return None

    def graph_snapshot(self, program_id: str) -> dict[str, Any]:
        state = self.load(program_id)
        if not state:
            return {"program_id": program_id, "nodes": [], "edges": [], "status": "idle"}
        return {
            "program_id": program_id,
            "linked_thread_ids": state.linked_thread_ids,
            "nodes": [n.to_dict() for n in state.nodes],
            "edges": state.edges,
            "status": state.dag_status,
        }
