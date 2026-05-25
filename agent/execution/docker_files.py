from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.config import Config
from tools.files import write_file
from tools.patch import apply_patch


def docker_file_tools_enabled(config: Config) -> bool:
    return (
        config.execution.backend == "docker"
        and config.execution.docker.file_tools_in_container
    )


def docker_write_file(cwd: Path, path: str, content: str, config: Config) -> tuple[str, dict]:
    """Write via host mount path (same volume as docker run_command)."""
    result = write_file(cwd, path, content)
    meta = {
        "backend": "docker",
        "via_mount": True,
        "workspace_mount": config.execution.workspace_mount,
        "image": config.execution.docker_image_override or config.execution.default_image,
    }
    return result, meta


def docker_apply_patch(cwd: Path, patch_text: str, config: Config) -> tuple[Any, dict]:
    outcome = apply_patch(cwd, patch_text)
    meta = {
        "backend": "docker",
        "via_mount": True,
        "workspace_mount": config.execution.workspace_mount,
        "image": config.execution.docker_image_override or config.execution.default_image,
    }
    return outcome, meta
