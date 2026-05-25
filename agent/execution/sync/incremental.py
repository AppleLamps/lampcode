from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.config import Config
from agent.execution.sync.conflicts import (
    SyncPlanResult,
    apply_conflict_strategy,
    classify_sync,
)
from agent.execution.sync.manifest import (
    SyncManifest,
    load_manifest,
    manifest_file_path,
    scan_local_manifest,
)
from agent.execution.sync.planner import (
    DEFAULT_EXCLUDES,
    build_remote_spec,
    check_transport_available,
    select_transport,
)
from agent.execution.sync.state import SyncStateStore, ThreadSyncState


@dataclass
class IncrementalSyncPlan:
    mode: str  # full | incremental
    transport: str
    local_cwd: Path
    remote_spec: str
    excludes: list[str]
    classification: SyncPlanResult
    bytes_estimated: int
    file_count: int
    local_manifest: SyncManifest
    remote_manifest: SyncManifest
    resolved_conflicts: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.resolved_conflicts is None:
            self.resolved_conflicts = []

    @property
    def counts(self) -> dict[str, int]:
        return self.classification.counts


def effective_sync_mode(config: Config, *, manifest_on_disk: bool) -> str:
    sync = config.execution.ssh.sync
    if sync.mode == "full":
        return "full"
    if not manifest_on_disk:
        return "full"
    return "incremental"


def build_incremental_plan(
    config: Config,
    direction: str,
    *,
    thread_id: str | None = None,
    remote_manifest: SyncManifest | None = None,
    force: bool = False,
) -> tuple[IncrementalSyncPlan | None, str | None]:
    ssh = config.execution.ssh
    sync = ssh.sync
    if not ssh.sync_enabled and direction == "push" and not force:
        return None, "sync disabled"

    excludes = list(sync.exclude or DEFAULT_EXCLUDES)
    cwd = config.cwd.resolve()
    manifest_path = manifest_file_path(cwd, sync.manifest_path)
    manifest_on_disk = manifest_path.is_file()
    local_manifest = scan_local_manifest(
        cwd,
        excludes=excludes,
        include_dotfiles=sync.include_dotfiles,
    )

    mode = effective_sync_mode(config, manifest_on_disk=manifest_on_disk)
    if mode == "full":
        return None, None  # caller falls back to Phase 8 plan_sync

    if remote_manifest is None:
        remote_manifest = SyncManifest()

    state = SyncStateStore().load(thread_id or "default")
    classification = classify_sync(
        local_manifest,
        remote_manifest,
        state,
        hash_on_conflict=sync.hash_on_conflict,
    )

    strategy = sync.conflict_strategy
    if classification.conflict and strategy != "prompt":
        classification, resolved, err = apply_conflict_strategy(classification, strategy)
        if err:
            return None, err
    elif classification.conflict and strategy == "abort":
        return None, f"aborted: {len(classification.conflict)} conflicts"

    total_files = len(classification.push) + len(classification.pull) + len(classification.conflict)
    if total_files > sync.max_files_per_sync:
        return None, (
            f"Sync would touch {total_files} files, exceeds max_files_per_sync "
            f"({sync.max_files_per_sync})"
        )

    bytes_est = 0
    for rel in classification.push:
        entry = local_manifest.files.get(rel)
        if entry:
            bytes_est += entry.size

    transport = select_transport(sync)
    return IncrementalSyncPlan(
        mode="incremental",
        transport=transport,
        local_cwd=cwd,
        remote_spec=build_remote_spec(config),
        excludes=excludes,
        classification=classification,
        bytes_estimated=bytes_est,
        file_count=total_files,
        local_manifest=local_manifest,
        remote_manifest=remote_manifest,
    ), None


def plan_dry_run(
    config: Config,
    *,
    thread_id: str | None = None,
    remote_manifest: SyncManifest | None = None,
) -> dict:
    """Dry-run sync plan for CLI `agent sync plan`."""
    ssh = config.execution.ssh
    sync = ssh.sync
    excludes = list(sync.exclude or DEFAULT_EXCLUDES)
    cwd = config.cwd.resolve()
    manifest_path = manifest_file_path(cwd, sync.manifest_path)
    manifest_on_disk = manifest_path.is_file()
    local_manifest = scan_local_manifest(
        cwd,
        excludes=excludes,
        include_dotfiles=sync.include_dotfiles,
    )
    has_manifest = manifest_on_disk

    mode = effective_sync_mode(config, manifest_on_disk=manifest_on_disk)
    if remote_manifest is None:
        remote_manifest = SyncManifest()

    state = SyncStateStore().load(thread_id or "default")
    classification = classify_sync(
        local_manifest,
        remote_manifest,
        state,
        hash_on_conflict=sync.hash_on_conflict,
    )

    return {
        "mode": mode,
        "manifest_exists": has_manifest,
        "transport": select_transport(sync),
        "counts": classification.counts,
        "conflicts": classification.conflict,
        "push": classification.push,
        "pull": classification.pull,
        "summary": classification.summary_line(),
    }
