from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.execution.sync.manifest import ManifestEntry, SyncManifest, file_hash
from agent.execution.sync.state import FileSyncState, ThreadSyncState


@dataclass
class SyncPlanResult:
    unchanged: list[str] = field(default_factory=list)
    push: list[str] = field(default_factory=list)
    pull: list[str] = field(default_factory=list)
    conflict: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    new_local: list[str] = field(default_factory=list)
    new_remote: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "unchanged": len(self.unchanged),
            "push": len(self.push),
            "pull": len(self.pull),
            "conflict": len(self.conflict),
            "excluded": len(self.excluded),
            "new_local": len(self.new_local),
            "new_remote": len(self.new_remote),
        }

    def summary_line(self) -> str:
        c = self.counts
        parts = []
        if c["push"]:
            parts.append(f"push {c['push']}")
        if c["pull"]:
            parts.append(f"pull {c['pull']}")
        if c["conflict"]:
            parts.append(f"conflicts {c['conflict']}")
        if c["new_local"]:
            parts.append(f"new local {c['new_local']}")
        if c["new_remote"]:
            parts.append(f"new remote {c['new_remote']}")
        return ", ".join(parts) if parts else "no changes"


def _entry_changed(
    current: ManifestEntry | None,
    baseline: ManifestEntry | None,
    *,
    use_hash: bool = False,
) -> bool:
    if current is None and baseline is None:
        return False
    if current is None or baseline is None:
        return True
    if use_hash and current.hash and baseline.hash:
        return current.hash != baseline.hash
    return current.mtime != baseline.mtime or current.size != baseline.size


def classify_sync(
    local: SyncManifest,
    remote: SyncManifest,
    state: ThreadSyncState,
    *,
    hash_on_conflict: bool = True,
) -> SyncPlanResult:
    """Classify files for incremental sync."""
    result = SyncPlanResult()
    all_paths = set(local.files) | set(remote.files) | set(state.files)

    for rel in sorted(all_paths):
        loc = local.files.get(rel)
        rem = remote.files.get(rel)
        prev = state.files.get(rel)

        if loc is None and rem is None:
            continue
        if loc is None:
            result.pull.append(rel)
            result.new_remote.append(rel)
            continue
        if rem is None:
            result.push.append(rel)
            result.new_local.append(rel)
            continue

        if prev is None:
            if loc.mtime == rem.mtime and loc.size == rem.size:
                result.unchanged.append(rel)
            elif loc.mtime > rem.mtime:
                result.push.append(rel)
            elif rem.mtime > loc.mtime:
                result.pull.append(rel)
            elif loc.size != rem.size:
                result.conflict.append(rel)
            else:
                result.unchanged.append(rel)
            continue

        local_changed = _entry_changed(loc, _baseline_local(prev, rem), use_hash=hash_on_conflict)
        remote_changed = _entry_changed(rem, _baseline_remote(prev, loc), use_hash=hash_on_conflict)

        if local_changed and remote_changed:
            result.conflict.append(rel)
        elif local_changed:
            result.push.append(rel)
        elif remote_changed:
            result.pull.append(rel)
        else:
            result.unchanged.append(rel)

    return result


def _baseline_local(prev: FileSyncState | None, remote: ManifestEntry) -> ManifestEntry | None:
    if prev and prev.local_mtime is not None and prev.local_size is not None:
        return ManifestEntry(
            mtime=prev.local_mtime,
            size=prev.local_size,
            hash=prev.local_hash,
        )
    return remote


def _baseline_remote(prev: FileSyncState | None, local: ManifestEntry) -> ManifestEntry | None:
    if prev and prev.remote_mtime is not None and prev.remote_size is not None:
        return ManifestEntry(
            mtime=prev.remote_mtime,
            size=prev.remote_size,
            hash=prev.remote_hash,
        )
    return local


def apply_conflict_strategy(
    plan: SyncPlanResult,
    strategy: str,
) -> tuple[SyncPlanResult, list[str], str | None]:
    """Apply conflict_strategy; returns (updated plan, resolved paths, error)."""
    if not plan.conflict:
        return plan, [], None
    if strategy == "abort":
        return plan, [], f"aborted: {len(plan.conflict)} conflicts"
    if strategy == "local-wins":
        plan.push.extend(plan.conflict)
        resolved = list(plan.conflict)
        plan.conflict = []
        return plan, resolved, None
    if strategy == "remote-wins":
        plan.pull.extend(plan.conflict)
        resolved = list(plan.conflict)
        plan.conflict = []
        return plan, resolved, None
    if strategy == "prompt":
        return plan, [], None
    return plan, [], f"unknown conflict strategy: {strategy}"


def resolve_single_conflict(
    plan: SyncPlanResult,
    path: str,
    strategy: str,
) -> bool:
    if path not in plan.conflict:
        return False
    plan.conflict.remove(path)
    if strategy == "local-wins":
        plan.push.append(path)
    elif strategy == "remote-wins":
        plan.pull.append(path)
    elif strategy == "skip":
        pass
    else:
        plan.conflict.append(path)
        return False
    return True


def update_state_after_sync(
    state: ThreadSyncState,
    local: SyncManifest,
    remote: SyncManifest,
    *,
    pushed: list[str],
    pulled: list[str],
    direction: str,
) -> None:
    now = SyncManifest().generated_at
    for rel in pushed:
        loc = local.files.get(rel)
        rem = remote.files.get(rel)
        entry = state.files.setdefault(rel, FileSyncState(path=rel))
        if loc:
            entry.local_mtime = loc.mtime
            entry.local_size = loc.size
            entry.local_hash = loc.hash
        if rem:
            entry.remote_mtime = rem.mtime
            entry.remote_size = rem.size
            entry.remote_hash = rem.hash
        entry.last_synced_at = now
        entry.last_direction = direction
    for rel in pulled:
        loc = local.files.get(rel)
        rem = remote.files.get(rel)
        entry = state.files.setdefault(rel, FileSyncState(path=rel))
        if loc:
            entry.local_mtime = loc.mtime
            entry.local_size = loc.size
            entry.local_hash = loc.hash
        if rem:
            entry.remote_mtime = rem.mtime
            entry.remote_size = rem.size
            entry.remote_hash = rem.hash
        entry.last_synced_at = now
        entry.last_direction = direction


def compute_local_hash_for_path(cwd: Path, rel: str) -> str | None:
    path = cwd / rel
    if not path.is_file():
        return None
    try:
        return file_hash(path)
    except OSError:
        return None
