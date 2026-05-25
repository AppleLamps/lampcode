from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.events import EventEmitter
from agent.execution.sync.base import SyncResult
from agent.execution.sync.conflicts import (
    apply_conflict_strategy,
    resolve_single_conflict,
    update_state_after_sync,
)
from agent.execution.sync.incremental import (
    IncrementalSyncPlan,
    build_incremental_plan,
    plan_dry_run,
)
from agent.execution.sync.manifest import (
    manifest_file_path,
    save_manifest,
    scan_local_manifest,
)
from agent.execution.sync.planner import estimate_sync_size, plan_sync
from agent.execution.sync.rsync import RsyncTransport
from agent.execution.sync.scp import ScpTransport
from agent.execution.sync.state import SyncStateStore
from agent.metrics import MetricsCollector
from agent.models import WorkspaceSyncItem
from agent.session import HarnessSession


def _get_transport(config: Config, name: str, runner=None):
    if name == "rsync":
        return RsyncTransport(config, runner=runner)
    return ScpTransport(config, runner=runner)


def sync_status(config: Config) -> dict:
    ssh = config.execution.ssh
    sync = ssh.sync
    excludes = list(sync.exclude)
    bytes_est, file_count = estimate_sync_size(
        config.cwd,
        excludes=excludes,
        include_dotfiles=sync.include_dotfiles,
    )
    from agent.execution.sync.planner import detect_sync_tools, select_transport

    plan = plan_dry_run(config)
    return {
        "sync_enabled": ssh.sync_enabled,
        "sync_mode": ssh.sync_mode,
        "sync_incremental_mode": sync.mode,
        "transport": select_transport(sync),
        "tools": detect_sync_tools(),
        "cwd": str(config.cwd),
        "remote_workspace": ssh.remote_workspace,
        "bytes_estimated": bytes_est,
        "file_count": file_count,
        "max_upload_mb": sync.max_upload_mb,
        "plan": plan,
    }


def run_sync_plan(
    config: Config,
    *,
    thread_id: str | None = None,
    remote_manifest=None,
) -> dict:
    return plan_dry_run(config, thread_id=thread_id, remote_manifest=remote_manifest)


def resolve_sync_path(
    config: Config,
    path: str,
    strategy: str,
    *,
    thread_id: str | None = None,
) -> dict:
    plan, err = build_incremental_plan(
        config, "push", thread_id=thread_id, force=True
    )
    if plan is None:
        return {"ok": False, "error": err or "incremental plan unavailable"}
    ok = resolve_single_conflict(plan.classification, path, strategy)
    if not ok:
        return {"ok": False, "error": f"path not in conflicts: {path}"}
    return {"ok": True, "path": path, "strategy": strategy, "plan": plan.counts}


def _resolve_conflicts_prompt(
    plan: IncrementalSyncPlan,
    conflict_fn: Callable[[str], str | None],
) -> tuple[IncrementalSyncPlan, str | None]:
    for rel in list(plan.classification.conflict):
        choice = conflict_fn(rel)
        if choice is None:
            return plan, f"conflict resolution cancelled for {rel}"
        if choice == "abort":
            return plan, f"aborted at conflict {rel}"
        resolve_single_conflict(plan.classification, rel, choice)
    plan.classification.conflict = []
    return plan, None


def _run_incremental(
    config: Config,
    plan: IncrementalSyncPlan,
    direction: str,
    *,
    runner=None,
) -> SyncResult:
    transport = _get_transport(config, plan.transport, runner=runner)
    files = plan.classification.push if direction == "push" else plan.classification.pull
    if not files:
        return SyncResult(
            ok=True,
            direction=direction,
            transport=plan.transport,
            files=0,
            summary="no files to transfer",
            skipped=True,
        )
    if hasattr(transport, "push_files") and direction == "push":
        return transport.push_files(plan.local_cwd, files, runner=runner)
    if hasattr(transport, "pull_files") and direction == "pull":
        return transport.pull_files(plan.local_cwd, files, runner=runner)
    if direction == "push":
        return transport.push(
            plan.local_cwd,
            plan.remote_spec,
            excludes=plan.excludes,
            delete_remote_extra=False,
            runner=runner,
        )
    return transport.pull(
        plan.local_cwd,
        plan.remote_spec,
        excludes=plan.excludes,
        runner=runner,
    )


def _finalize_incremental(
    config: Config,
    plan: IncrementalSyncPlan,
    direction: str,
    *,
    thread_id: str | None,
    pushed: list[str] | None = None,
    pulled: list[str] | None = None,
) -> None:
    sync = config.execution.ssh.sync
    manifest_path = manifest_file_path(plan.local_cwd, sync.manifest_path)
    local_manifest = scan_local_manifest(
        plan.local_cwd,
        excludes=plan.excludes,
        include_dotfiles=sync.include_dotfiles,
    )
    save_manifest(manifest_path, local_manifest)
    store = SyncStateStore()
    state = store.load(thread_id or "default")
    update_state_after_sync(
        state,
        local_manifest,
        plan.remote_manifest,
        pushed=pushed or [],
        pulled=pulled or [],
        direction=direction,
    )
    store.save(state)


def run_sync_push(
    config: Config,
    *,
    force: bool = False,
    incremental: bool = True,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
    remote_manifest=None,
    conflict_fn: Callable[[str], str | None] | None = None,
) -> tuple[SyncResult, WorkspaceSyncItem]:
    inc_plan, inc_err = (None, None)
    if incremental:
        inc_plan, inc_err = build_incremental_plan(
            config,
            "push",
            thread_id=thread_id,
            remote_manifest=remote_manifest,
            force=force,
        )
        if inc_err and inc_plan is None:
            item = WorkspaceSyncItem(
                direction="push",
                transport="scp",
                status="failed",
                summary=inc_err,
            )
            if emitter and thread_id:
                emitter.execution_sync_failed(thread_id, turn_id, reason=inc_err)
            return SyncResult(
                ok=False,
                direction="push",
                transport="scp",
                summary=inc_err,
                error=inc_err,
            ), item

    if inc_plan is not None:
        return _run_incremental_push(
            config,
            inc_plan,
            force=force,
            emitter=emitter,
            thread_id=thread_id,
            turn_id=turn_id,
            runner=runner,
            conflict_fn=conflict_fn,
        )

    plan, err = plan_sync(config, "push", force=force)
    item = WorkspaceSyncItem(
        direction="push",
        transport="scp",
        status="skipped",
        summary=err or "skipped",
    )
    if plan is None:
        if err:
            item.status = "failed"
            item.summary = err
            if emitter and thread_id:
                emitter.execution_sync_failed(thread_id, turn_id, reason=err)
        return SyncResult(
            ok=False,
            direction="push",
            transport="scp",
            skipped=True,
            summary=err or "skipped",
            error=err,
        ), item

    item.transport = plan.transport  # type: ignore[assignment]
    if emitter and thread_id:
        emitter.execution_sync_started(
            thread_id,
            turn_id,
            direction="push",
            transport=plan.transport,
            bytes_estimated=plan.bytes_estimated,
        )

    transport = _get_transport(config, plan.transport, runner=runner)
    result = transport.push(
        plan.local_cwd,
        plan.remote_spec,
        excludes=plan.excludes,
        delete_remote_extra=plan.delete_remote_extra,
        runner=runner,
    )
    result.files = plan.file_count
    result.bytes_transferred = plan.bytes_estimated
    item.status = "completed" if result.ok else "failed"
    item.summary = result.summary
    item.files = result.files
    item.bytes_transferred = result.bytes_transferred
    item.duration_ms = result.duration_ms
    if result.ok and emitter and thread_id:
        emitter.execution_sync_completed(
            thread_id,
            turn_id,
            direction="push",
            transport=plan.transport,
            files=result.files,
            bytes_transferred=result.bytes_transferred,
            duration_ms=result.duration_ms,
        )
    elif not result.ok and emitter and thread_id:
        emitter.execution_sync_failed(thread_id, turn_id, reason=result.error or result.summary)
    if result.ok:
        MetricsCollector.global_collector().inc("sync_bytes_up", result.bytes_transferred)
    return result, item


def _run_incremental_push(
    config: Config,
    plan: IncrementalSyncPlan,
    *,
    force: bool,
    emitter: EventEmitter | None,
    thread_id: str | None,
    turn_id: str | None,
    runner,
    conflict_fn: Callable[[str], str | None] | None,
) -> tuple[SyncResult, WorkspaceSyncItem]:
    sync = config.execution.ssh.sync
    classification = plan.classification
    if classification.conflict:
        MetricsCollector.global_collector().inc(
            "sync_conflicts", len(classification.conflict)
        )
        if sync.conflict_strategy == "prompt":
            if conflict_fn:
                plan, err = _resolve_conflicts_prompt(plan, conflict_fn)
                if err:
                    item = WorkspaceSyncItem(
                        direction="push",
                        transport=plan.transport,  # type: ignore[arg-type]
                        status="failed",
                        summary=err,
                        plan=plan.counts,
                    )
                    if emitter and thread_id:
                        emitter.execution_sync_failed(thread_id, turn_id, reason=err)
                    return SyncResult(
                        ok=False,
                        direction="push",
                        transport=plan.transport,
                        error=err,
                        summary=err,
                    ), item
            else:
                err = f"{len(classification.conflict)} unresolved conflicts (use sync resolve or prompt)"
                item = WorkspaceSyncItem(
                    direction="push",
                    transport=plan.transport,  # type: ignore[arg-type]
                    status="failed",
                    summary=err,
                    plan=plan.counts,
                )
                if emitter and thread_id:
                    emitter.execution_sync_plan(
                        thread_id, turn_id, counts=plan.counts, conflicts=classification.conflict
                    )
                    emitter.execution_sync_failed(thread_id, turn_id, reason=err)
                return SyncResult(
                    ok=False,
                    direction="push",
                    transport=plan.transport,
                    error=err,
                    summary=err,
                ), item
        else:
            classification, _, err = apply_conflict_strategy(
                classification, sync.conflict_strategy
            )
            plan.classification = classification
            if err:
                item = WorkspaceSyncItem(
                    direction="push",
                    transport=plan.transport,  # type: ignore[arg-type]
                    status="failed",
                    summary=err,
                    plan=plan.counts,
                )
                return SyncResult(
                    ok=False,
                    direction="push",
                    transport=plan.transport,
                    error=err,
                    summary=err,
                ), item

    max_bytes = sync.max_upload_mb * 1024 * 1024
    if plan.bytes_estimated > max_bytes and not force and not config.force_sync:
        mb = plan.bytes_estimated / (1024 * 1024)
        err = (
            f"Estimated upload {mb:.1f}MB exceeds limit {sync.max_upload_mb}MB "
            f"(use --force-sync to override)"
        )
        item = WorkspaceSyncItem(
            direction="push",
            transport=plan.transport,  # type: ignore[arg-type]
            status="failed",
            summary=err,
            plan=plan.counts,
        )
        return SyncResult(
            ok=False,
            direction="push",
            transport=plan.transport,
            error=err,
            summary=err,
        ), item

    if emitter and thread_id:
        emitter.execution_sync_plan(
            thread_id, turn_id, counts=plan.counts, conflicts=plan.classification.conflict
        )
        emitter.execution_sync_started(
            thread_id,
            turn_id,
            direction="push",
            transport=plan.transport,
            bytes_estimated=plan.bytes_estimated,
        )

    result = _run_incremental(config, plan, "push", runner=runner)
    summary = classification.summary_line()
    if plan.resolved_conflicts:
        summary += f" (resolved {', '.join(plan.resolved_conflicts)})"
    item = WorkspaceSyncItem(
        direction="push",
        transport=plan.transport,  # type: ignore[arg-type]
        status="completed" if result.ok else "failed",
        summary=summary if result.ok else (result.error or result.summary),
        files=result.files,
        bytes_transferred=result.bytes_transferred,
        duration_ms=result.duration_ms,
        plan=plan.counts,
    )
    if result.ok:
        _finalize_incremental(
            config,
            plan,
            "push",
            thread_id=thread_id,
            pushed=plan.classification.push,
        )
        if emitter and thread_id:
            emitter.execution_sync_completed(
                thread_id,
                turn_id,
                direction="push",
                transport=plan.transport,
                files=result.files,
                bytes_transferred=result.bytes_transferred,
                duration_ms=result.duration_ms,
            )
        MetricsCollector.global_collector().inc("sync_bytes_up", result.bytes_transferred)
    elif emitter and thread_id:
        emitter.execution_sync_failed(thread_id, turn_id, reason=result.error or result.summary)
    return result, item


def run_sync_pull(
    config: Config,
    *,
    incremental: bool = True,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
    remote_manifest=None,
    conflict_fn: Callable[[str], str | None] | None = None,
) -> tuple[SyncResult, WorkspaceSyncItem]:
    inc_plan, inc_err = (None, None)
    if incremental:
        inc_plan, inc_err = build_incremental_plan(
            config,
            "pull",
            thread_id=thread_id,
            remote_manifest=remote_manifest,
            force=True,
        )

    if inc_plan is not None:
        sync = config.execution.ssh.sync
        if inc_plan.classification.conflict and sync.conflict_strategy == "prompt" and conflict_fn:
            inc_plan, err = _resolve_conflicts_prompt(inc_plan, conflict_fn)
            if err:
                item = WorkspaceSyncItem(
                    direction="pull",
                    transport=inc_plan.transport,  # type: ignore[arg-type]
                    status="failed",
                    summary=err,
                    plan=inc_plan.counts,
                )
                return SyncResult(
                    ok=False,
                    direction="pull",
                    transport=inc_plan.transport,
                    error=err,
                    summary=err,
                ), item
        elif inc_plan.classification.conflict:
            inc_plan.classification, _, err = apply_conflict_strategy(
                inc_plan.classification, sync.conflict_strategy
            )
            if err:
                item = WorkspaceSyncItem(
                    direction="pull",
                    transport=inc_plan.transport,  # type: ignore[arg-type]
                    status="failed",
                    summary=err,
                    plan=inc_plan.counts,
                )
                return SyncResult(
                    ok=False,
                    direction="pull",
                    transport=inc_plan.transport,
                    error=err,
                    summary=err,
                ), item

        if emitter and thread_id:
            emitter.execution_sync_plan(
                thread_id, turn_id, counts=inc_plan.counts, conflicts=inc_plan.classification.conflict
            )
            emitter.execution_sync_started(
                thread_id,
                turn_id,
                direction="pull",
                transport=inc_plan.transport,
                bytes_estimated=inc_plan.bytes_estimated,
            )
        result = _run_incremental(config, inc_plan, "pull", runner=runner)
        item = WorkspaceSyncItem(
            direction="pull",
            transport=inc_plan.transport,  # type: ignore[arg-type]
            status="completed" if result.ok else "failed",
            summary=inc_plan.classification.summary_line() if result.ok else (result.error or result.summary),
            files=result.files,
            bytes_transferred=result.bytes_transferred,
            duration_ms=result.duration_ms,
            plan=inc_plan.counts,
        )
        if result.ok:
            _finalize_incremental(
                config,
                inc_plan,
                "pull",
                thread_id=thread_id,
                pulled=inc_plan.classification.pull,
            )
            if emitter and thread_id:
                emitter.execution_sync_completed(
                    thread_id,
                    turn_id,
                    direction="pull",
                    transport=inc_plan.transport,
                    files=result.files,
                    bytes_transferred=result.bytes_transferred,
                    duration_ms=result.duration_ms,
                )
            MetricsCollector.global_collector().inc("sync_bytes_down", result.bytes_transferred)
        elif emitter and thread_id:
            emitter.execution_sync_failed(thread_id, turn_id, reason=result.error or result.summary)
        return result, item

    plan, err = plan_sync(config, "pull", force=True)
    item = WorkspaceSyncItem(
        direction="pull",
        transport="scp",
        status="skipped",
        summary=err or "skipped",
    )
    if plan is None:
        item.status = "failed"
        item.summary = err or "plan failed"
        return SyncResult(
            ok=False,
            direction="pull",
            transport="scp",
            skipped=True,
            error=err,
        ), item

    item.transport = plan.transport  # type: ignore[assignment]
    if emitter and thread_id:
        emitter.execution_sync_started(
            thread_id,
            turn_id,
            direction="pull",
            transport=plan.transport,
            bytes_estimated=plan.bytes_estimated,
        )

    transport = _get_transport(config, plan.transport, runner=runner)
    result = transport.pull(
        plan.local_cwd,
        plan.remote_spec,
        excludes=plan.excludes,
        runner=runner,
    )
    result.files = plan.file_count
    result.bytes_transferred = plan.bytes_estimated
    item.status = "completed" if result.ok else "failed"
    item.summary = result.summary
    item.files = result.files
    item.bytes_transferred = result.bytes_transferred
    item.duration_ms = result.duration_ms
    if result.ok and emitter and thread_id:
        emitter.execution_sync_completed(
            thread_id,
            turn_id,
            direction="pull",
            transport=plan.transport,
            files=result.files,
            bytes_transferred=result.bytes_transferred,
            duration_ms=result.duration_ms,
        )
    elif not result.ok and emitter and thread_id:
        emitter.execution_sync_failed(thread_id, turn_id, reason=result.error or result.summary)
    if result.ok:
        MetricsCollector.global_collector().inc("sync_bytes_down", result.bytes_transferred)
    return result, item


def maybe_sync_turn_start(
    config: Config,
    session: HarnessSession,
    *,
    force: bool = False,
    approve_fn: Callable[[], bool] | None = None,
    conflict_fn: Callable[[str], str | None] | None = None,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
    remote_manifest=None,
) -> tuple[SyncResult | None, WorkspaceSyncItem | None]:
    ssh = config.execution.ssh
    if config.execution.backend != "ssh" or not ssh.sync_enabled:
        return None, None
    if ssh.sync_on != "turn_start":
        return None, None
    if ssh.sync_mode == "manual":
        return None, None
    if not session.sync_push_approved and not session.session_auto_approve:
        if approve_fn and not approve_fn():
            item = WorkspaceSyncItem(
                direction="push",
                transport="scp",
                status="skipped",
                summary="User denied sync push",
            )
            return None, item
        session.sync_push_approved = True

    result, item = run_sync_push(
        config,
        force=force or config.force_sync,
        emitter=emitter,
        thread_id=thread_id,
        turn_id=turn_id,
        runner=runner,
        conflict_fn=conflict_fn,
        remote_manifest=remote_manifest,
    )
    return result, item


def maybe_sync_turn_end(
    config: Config,
    turn_status: str,
    *,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
    remote_manifest=None,
    conflict_fn: Callable[[str], str | None] | None = None,
) -> tuple[SyncResult | None, WorkspaceSyncItem | None]:
    ssh = config.execution.ssh
    if config.execution.backend != "ssh" or not ssh.sync_enabled:
        return None, None
    if turn_status != "completed":
        return None, None
    if ssh.sync_mode != "push-pull" or not ssh.pull_on_turn_end:
        return None, None
    return run_sync_pull(
        config,
        emitter=emitter,
        thread_id=thread_id,
        turn_id=turn_id,
        runner=runner,
        remote_manifest=remote_manifest,
        conflict_fn=conflict_fn,
    )
