from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.events import EventEmitter
from agent.execution.sync.base import SyncResult
from agent.execution.sync.planner import estimate_sync_size, plan_sync
from agent.execution.sync.rsync import RsyncTransport
from agent.execution.sync.scp import ScpTransport
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

    return {
        "sync_enabled": ssh.sync_enabled,
        "sync_mode": ssh.sync_mode,
        "transport": select_transport(sync),
        "tools": detect_sync_tools(),
        "cwd": str(config.cwd),
        "remote_workspace": ssh.remote_workspace,
        "bytes_estimated": bytes_est,
        "file_count": file_count,
        "max_upload_mb": sync.max_upload_mb,
    }


def run_sync_push(
    config: Config,
    *,
    force: bool = False,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
) -> tuple[SyncResult, WorkspaceSyncItem]:
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

    if emitter and thread_id:
        if result.ok:
            emitter.execution_sync_completed(
                thread_id,
                turn_id,
                direction="push",
                transport=plan.transport,
                files=result.files,
                bytes_transferred=result.bytes_transferred,
                duration_ms=result.duration_ms,
            )
        else:
            emitter.execution_sync_failed(
                thread_id, turn_id, reason=result.error or result.summary
            )
    return result, item


def run_sync_pull(
    config: Config,
    *,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
) -> tuple[SyncResult, WorkspaceSyncItem]:
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

    if emitter and thread_id:
        if result.ok:
            emitter.execution_sync_completed(
                thread_id,
                turn_id,
                direction="pull",
                transport=plan.transport,
                files=result.files,
                bytes_transferred=result.bytes_transferred,
                duration_ms=result.duration_ms,
            )
        else:
            emitter.execution_sync_failed(
                thread_id, turn_id, reason=result.error or result.summary
            )
    return result, item


def maybe_sync_turn_start(
    config: Config,
    session: HarnessSession,
    *,
    force: bool = False,
    approve_fn: Callable[[], bool] | None = None,
    emitter: EventEmitter | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
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
    )
