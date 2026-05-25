from __future__ import annotations

from pathlib import Path


def host_to_container_path(host_cwd: Path, container_mount: str) -> str:
    return container_mount.rstrip("/")


def build_volume_mount(
    host_cwd: Path,
    container_mount: str,
    *,
    read_only: bool = False,
) -> str:
    """Build docker -v argument: host:container[:ro]."""
    host = str(host_cwd.resolve())
    mount = f"{host}:{container_mount.rstrip('/')}"
    if read_only:
        mount += ":ro"
    return mount


def container_workdir(
    workspace_mount: str,
    workdir: str | None = None,
) -> str:
    base = workspace_mount.rstrip("/")
    if not workdir:
        return base
    rel = workdir.replace("\\", "/").strip("/")
    return f"{base}/{rel}" if rel else base
