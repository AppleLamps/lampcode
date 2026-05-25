from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from agent.config import Config
from agent.execution.ssh import build_remote_shell_command, build_ssh_argv
from agent.execution.sync.manifest import SyncManifest, load_manifest, save_manifest
from agent.execution.sync.planner import subprocess_runner
from agent.execution.sync.state import SyncStateStore, ThreadSyncState


@dataclass
class RemoteFetchResult:
    ok: bool
    manifest: SyncManifest
    missing: bool = False
    error: str | None = None
    argv: list[str] | None = None


def remote_manifest_rel_path(config: Config) -> str:
    rel = config.execution.ssh.sync.manifest_path.lstrip("/")
    return rel.replace("\\", "/")


def remote_manifest_full_path(config: Config) -> str:
    base = config.execution.ssh.remote_workspace.rstrip("/")
    return f"{base}/{remote_manifest_rel_path(config)}"


def build_fetch_manifest_command(config: Config) -> str:
    path = remote_manifest_full_path(config)
    return f"cat {shlex.quote(path)}"


def build_remote_scan_command(config: Config, *, max_files: int) -> str:
    base = config.execution.ssh.remote_workspace.rstrip("/")
    sync = config.execution.ssh.sync
    wrapper = sync.remote_shell.strip() or "bash -lc"
    inner = (
        f"find {shlex.quote(base)} -type f 2>/dev/null | head -n {max_files} | "
        f"while IFS= read -r f; do "
        f"sz=$(stat -c %s \"$f\" 2>/dev/null || stat -f %z \"$f\" 2>/dev/null || echo 0); "
        f"mt=$(stat -c %Y \"$f\" 2>/dev/null || stat -f %m \"$f\" 2>/dev/null || echo 0); "
        f"rel=${{f#{base}/}}; echo \"$rel|$mt|$sz\"; done"
    )
    return f"{wrapper} {shlex.quote(inner)}"


def fetch_remote_manifest(
    config: Config,
    *,
    runner=None,
    emitter=None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> RemoteFetchResult:
    sync = config.execution.ssh.sync
    if not sync.fetch_remote_manifest:
        return RemoteFetchResult(ok=True, manifest=SyncManifest(), missing=True)

    run = runner or subprocess_runner
    remote_cmd = build_fetch_manifest_command(config)
    argv = build_ssh_argv(config, remote_cmd)
    try:
        proc = run(argv, timeout=config.execution.ssh.command_timeout_sec)
    except Exception as exc:
        return RemoteFetchResult(ok=False, manifest=SyncManifest(), error=str(exc), argv=argv)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if sync.on_remote_manifest_missing in ("create", "empty") and (
            "No such file" in err or "cannot open" in err.lower() or not err
        ):
            manifest = SyncManifest()
            if sync.on_remote_manifest_missing == "create":
                manifest.generated_at = SyncManifest().generated_at
            if emitter and thread_id:
                emitter.execution_sync_remote_manifest_fetched(
                    thread_id, turn_id, missing=True, files=0
                )
            return RemoteFetchResult(ok=True, manifest=manifest, missing=True, argv=argv)
        if sync.on_remote_manifest_missing == "abort":
            return RemoteFetchResult(
                ok=False,
                manifest=SyncManifest(),
                missing=True,
                error=err or "remote manifest missing",
                argv=argv,
            )
        return RemoteFetchResult(ok=True, manifest=SyncManifest(), missing=True, argv=argv)

    text = (proc.stdout or "").strip()
    if not text:
        manifest = SyncManifest()
        if emitter and thread_id:
            emitter.execution_sync_remote_manifest_fetched(
                thread_id, turn_id, missing=True, files=0
            )
        return RemoteFetchResult(ok=True, manifest=manifest, missing=True, argv=argv)

    try:
        data = json.loads(text)
        manifest = SyncManifest.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return RemoteFetchResult(
            ok=False,
            manifest=SyncManifest(),
            error=f"invalid remote manifest JSON: {exc}",
            argv=argv,
        )

    if emitter and thread_id:
        emitter.execution_sync_remote_manifest_fetched(
            thread_id, turn_id, missing=False, files=len(manifest.files)
        )
    return RemoteFetchResult(ok=True, manifest=manifest, missing=False, argv=argv)


def scan_remote_index(
    config: Config,
    *,
    runner=None,
    emitter=None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> tuple[SyncManifest, str | None]:
    sync = config.execution.ssh.sync
    run = runner or subprocess_runner
    remote_cmd = build_remote_scan_command(config, max_files=sync.remote_scan_max_files)
    argv = build_ssh_argv(config, remote_cmd)
    try:
        proc = run(argv, timeout=config.execution.ssh.command_timeout_sec)
    except Exception as exc:
        return SyncManifest(), str(exc)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "remote scan failed").strip()
        return SyncManifest(), err

    manifest = SyncManifest()
    from agent.execution.sync.manifest import ManifestEntry

    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        rel, mt, sz = line.split("|", 2)
        rel = rel.replace("\\", "/").lstrip("/")
        try:
            manifest.files[rel] = ManifestEntry(
                mtime=float(mt),
                size=int(float(sz)),
            )
        except ValueError:
            continue

    if emitter and thread_id:
        emitter.execution_sync_remote_scan_completed(
            thread_id, turn_id, files=len(manifest.files)
        )
    return manifest, None


def resolve_remote_manifest(
    config: Config,
    *,
    thread_id: str | None = None,
    turn_id: str | None = None,
    runner=None,
    emitter=None,
    remote_manifest: SyncManifest | None = None,
) -> tuple[SyncManifest | None, str | None]:
    """Fetch remote manifest when enabled; merge with optional scan fallback."""
    if remote_manifest is not None:
        return remote_manifest, None
    if config.execution.backend != "ssh":
        return SyncManifest(), None
    sync = config.execution.ssh.sync
    if not sync.fetch_remote_manifest:
        return SyncManifest(), None

    result = fetch_remote_manifest(
        config,
        runner=runner,
        emitter=emitter,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    if not result.ok:
        return None, result.error

    manifest = result.manifest
    if result.missing or not manifest.files:
        scanned, err = scan_remote_index(
            config,
            runner=runner,
            emitter=emitter,
            thread_id=thread_id,
            turn_id=turn_id,
        )
        if err and sync.on_remote_manifest_missing == "abort":
            return None, err
        if scanned.files:
            manifest = scanned
    return manifest, None


def remote_state_snapshot_path(thread_id: str) -> Path:
    from agent.execution.sync.state import sync_state_dir

    return sync_state_dir() / f"{thread_id}.remote.json"


def save_remote_state_snapshot(thread_id: str, manifest: SyncManifest) -> Path:
    path = remote_state_snapshot_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_manifest(path, manifest)
    return path


def load_remote_state_snapshot(thread_id: str) -> SyncManifest | None:
    path = remote_state_snapshot_path(thread_id)
    return load_manifest(path)


def replicate_remote_state_if_enabled(
    config: Config,
    thread_id: str | None,
    manifest: SyncManifest,
) -> None:
    if not thread_id or not config.execution.ssh.sync.replicate_remote_state:
        return
    save_remote_state_snapshot(thread_id, manifest)
