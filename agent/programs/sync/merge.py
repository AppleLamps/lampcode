from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.multi_agent.program_state import ProgramNode, ProgramState


@dataclass
class MergeResult:
    state: ProgramState
    conflicts: list[str]
    merged: bool


def merge_program_states(
    local: ProgramState | None,
    remote: ProgramState | None,
    *,
    last_sync_at: float = 0.0,
) -> MergeResult:
    if local is None and remote is None:
        raise ValueError("both states empty")
    if local is None and remote is not None:
        return MergeResult(state=remote, conflicts=[], merged=True)
    if remote is None and local is not None:
        return MergeResult(state=local, conflicts=[], merged=False)

    assert local is not None and remote is not None
    conflicts: list[str] = []
    local_nodes = {n.worker_id: n for n in local.nodes}
    remote_nodes = {n.worker_id: n for n in remote.nodes}

    for wid, ln in local_nodes.items():
        rn = remote_nodes.get(wid)
        if rn and last_sync_at > 0:
            if ln.updated_at > last_sync_at and rn.updated_at > last_sync_at and ln.status != rn.status:
                conflicts.append(wid)

    if remote.updated_at >= local.updated_at:
        winner = remote
    else:
        winner = local

    merged_nodes: dict[str, ProgramNode] = {}
    for wid in set(local_nodes) | set(remote_nodes):
        ln = local_nodes.get(wid)
        rn = remote_nodes.get(wid)
        if ln and rn:
            merged_nodes[wid] = ln if ln.updated_at >= rn.updated_at else rn
        elif ln:
            merged_nodes[wid] = ln
        elif rn:
            merged_nodes[wid] = rn

    merged_state = ProgramState(
        program_id=winner.program_id,
        linked_thread_ids=sorted(set(local.linked_thread_ids) | set(remote.linked_thread_ids)),
        nodes=list(merged_nodes.values()),
        edges=_merge_edges(local.edges, remote.edges),
        dag_status=winner.dag_status,
        updated_at=max(local.updated_at, remote.updated_at),
    )
    return MergeResult(state=merged_state, conflicts=conflicts, merged=True)


def _merge_edges(a: list[dict[str, str]], b: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for edge in a + b:
        key = (edge.get("from", ""), edge.get("to", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(edge))
    return out
