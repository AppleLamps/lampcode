from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class DagEdge:
    from_id: str
    to_id: str

    def to_dict(self) -> dict[str, str]:
        return {"from": self.from_id, "to": self.to_id}

    @classmethod
    def from_dict(cls, data: dict) -> DagEdge:
        return cls(from_id=str(data["from"]), to_id=str(data["to"]))


@dataclass
class WorkerDagSnapshot:
    nodes: list[dict]
    edges: list[dict]
    status: str = "running"

    def to_dict(self) -> dict:
        return {"nodes": self.nodes, "edges": self.edges, "status": self.status}


def edges_from_dependencies(
    worker_ids: Iterable[str],
    dependencies: dict[str, list[str]],
) -> list[DagEdge]:
    edges: list[DagEdge] = []
    for to_id, deps in dependencies.items():
        for from_id in deps:
            edges.append(DagEdge(from_id=from_id, to_id=to_id))
    return edges


def detect_cycle(
    edges: list[DagEdge],
    *,
    extra_from: str | None = None,
    extra_to: str | None = None,
) -> list[str] | None:
    """Return a cycle path if adding extra edge would create or expose a cycle."""
    adj: dict[str, list[str]] = {}
    nodes: set[str] = set()

    def _add(a: str, b: str) -> None:
        nodes.add(a)
        nodes.add(b)
        adj.setdefault(a, []).append(b)

    for edge in edges:
        _add(edge.from_id, edge.to_id)
    if extra_from and extra_to:
        _add(extra_from, extra_to)

    visited: set[str] = set()
    stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        visited.add(node)
        stack.add(node)
        path.append(node)
        for nxt in adj.get(node, []):
            if nxt not in visited:
                found = dfs(nxt)
                if found:
                    return found
            elif nxt in stack:
                idx = path.index(nxt)
                return path[idx:] + [nxt]
        path.pop()
        stack.remove(node)
        return None

    for node in sorted(nodes):
        if node not in visited:
            found = dfs(node)
            if found:
                return found
    return None


def deps_satisfied(
    worker_id: str,
    dependencies: dict[str, list[str]],
    statuses: dict[str, str],
) -> bool:
    for dep in dependencies.get(worker_id, []):
        if statuses.get(dep) != "completed":
            return False
    return True


def topological_order(node_ids: list[str], edges: list[DagEdge]) -> list[str]:
    deps_count: dict[str, int] = {n: 0 for n in node_ids}
    adj: dict[str, list[str]] = {n: [] for n in node_ids}
    for edge in edges:
        if edge.from_id in adj and edge.to_id in deps_count:
            adj[edge.from_id].append(edge.to_id)
            deps_count[edge.to_id] += 1
    ready = [n for n in node_ids if deps_count[n] == 0]
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in adj.get(node, []):
            deps_count[nxt] -= 1
            if deps_count[nxt] == 0:
                ready.append(nxt)
    if len(order) != len(node_ids):
        return node_ids
    return order


def aggregate_dependency_summaries(
    worker_id: str,
    dependencies: dict[str, list[str]],
    summaries: dict[str, str | None],
) -> str:
    parts: list[str] = []
    for dep in dependencies.get(worker_id, []):
        summary = summaries.get(dep) or ""
        if summary:
            parts.append(f"[{dep}] {summary[:500]}")
    if not parts:
        return ""
    return "Dependency context:\n" + "\n".join(parts) + "\n\n"
