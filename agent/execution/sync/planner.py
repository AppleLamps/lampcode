from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent.config import Config
from agent.settings import SshSyncSettings


@dataclass
class SyncPlan:
    transport: str
    local_cwd: Path
    remote_spec: str
    excludes: list[str]
    bytes_estimated: int
    file_count: int
    delete_remote_extra: bool


DEFAULT_EXCLUDES = [
    ".git/objects",
    "__pycache__",
    ".venv",
    "node_modules",
    ".agent-cli",
]


def _should_exclude(rel: str, excludes: list[str], include_dotfiles: bool) -> bool:
    parts = Path(rel).parts
    if not include_dotfiles:
        for part in parts:
            if part.startswith(".") and part not in (".", ".."):
                return True
    norm = rel.replace("\\", "/")
    for pattern in excludes:
        p = pattern.replace("\\", "/").strip("/")
        if norm == p or norm.startswith(p + "/") or ("/" + p + "/") in ("/" + norm + "/"):
            return True
        if p in parts:
            return True
    return False


def estimate_sync_size(
    cwd: Path,
    *,
    excludes: list[str],
    include_dotfiles: bool = False,
) -> tuple[int, int]:
    total_bytes = 0
    file_count = 0
    cwd = cwd.resolve()
    if not cwd.is_dir():
        return 0, 0
    for path in cwd.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = str(path.relative_to(cwd))
        except ValueError:
            continue
        if _should_exclude(rel, excludes, include_dotfiles):
            continue
        try:
            total_bytes += path.stat().st_size
            file_count += 1
        except OSError:
            continue
    return total_bytes, file_count


def check_transport_available(name: str) -> tuple[bool, str]:
    if name == "rsync":
        path = shutil.which("rsync")
        return (True, path) if path else (False, "rsync not found on PATH")
    if name == "scp":
        path = shutil.which("scp")
        return (True, path) if path else (False, "scp not found on PATH")
    return False, f"unknown transport: {name}"


def select_transport(sync: SshSyncSettings) -> str:
    pref = sync.transport
    if pref == "rsync":
        ok, _ = check_transport_available("rsync")
        return "rsync" if ok else "scp"
    if pref == "scp":
        return "scp"
    ok, _ = check_transport_available("rsync")
    return "rsync" if ok else "scp"


def build_remote_spec(config: Config) -> str:
    ssh = config.execution.ssh
    host = f"{ssh.user}@{ssh.host}" if ssh.user else ssh.host
    remote = ssh.remote_workspace.rstrip("/") + "/"
    if ssh.port != 22:
        return f"ssh -p {ssh.port} {host}:{remote}"
    return f"{host}:{remote}"


def build_scp_remote_target(config: Config) -> str:
    ssh = config.execution.ssh
    host = f"{ssh.user}@{ssh.host}" if ssh.user else ssh.host
    remote = ssh.remote_workspace.rstrip("/")
    return f"{host}:{remote}"


def plan_sync(
    config: Config,
    direction: str,
    *,
    force: bool = False,
) -> tuple[SyncPlan | None, str | None]:
    ssh = config.execution.ssh
    sync = ssh.sync
    if not ssh.sync_enabled and direction == "push" and not force:
        return None, "sync disabled"

    excludes = list(sync.exclude or DEFAULT_EXCLUDES)
    bytes_est, file_count = estimate_sync_size(
        config.cwd,
        excludes=excludes,
        include_dotfiles=sync.include_dotfiles,
    )
    max_bytes = sync.max_upload_mb * 1024 * 1024
    if direction == "push" and bytes_est > max_bytes and not force:
        mb = bytes_est / (1024 * 1024)
        return None, (
            f"Estimated upload {mb:.1f}MB exceeds limit {sync.max_upload_mb}MB "
            f"(use --force-sync to override)"
        )

    transport = select_transport(sync)
    return SyncPlan(
        transport=transport,
        local_cwd=config.cwd.resolve(),
        remote_spec=build_remote_spec(config),
        excludes=excludes,
        bytes_estimated=bytes_est,
        file_count=file_count,
        delete_remote_extra=ssh.delete_remote_extra,
    ), None


def detect_sync_tools() -> dict[str, str]:
    out: dict[str, str] = {}
    for tool in ("rsync", "scp"):
        ok, msg = check_transport_available(tool)
        out[tool] = msg if ok else f"unavailable: {msg}"
    return out


def subprocess_runner(argv: list[str], timeout: int = 600):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
