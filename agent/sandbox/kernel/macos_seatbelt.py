from __future__ import annotations

import sys
from pathlib import Path
from agent.sandbox.kernel.base import KernelWrapResult
from agent.sandbox.kernel.policy import policy_label
from agent.sandbox.policy import SandboxMode


class MacosSeatbeltBackend:
    name = "seatbelt"

    def __init__(self, settings) -> None:
        self._settings = settings

    def available(self) -> bool:
        if sys.platform != "darwin":
            return False
        return Path(self._settings.macos.sandbox_exec).is_file()

    def wrap_command(
        self,
        *,
        cmd: str,
        cwd: str,
        sandbox_mode: str,
        settings,
    ) -> KernelWrapResult:
        mode = SandboxMode.from_str(sandbox_mode)
        profile = settings.macos.profile_template
        if mode == SandboxMode.READ_ONLY:
            profile = "read-only"
        sb = settings.macos.sandbox_exec
        cwd_path = str(Path(cwd).resolve())
        argv = [sb, "-p", _profile_for(mode, profile), "/bin/sh", "-c", cmd]
        return KernelWrapResult(
            applied=True,
            backend=self.name,
            argv=argv,
            shell=False,
            meta={"policy": policy_label(mode), "cwd": cwd_path, "profile": profile},
            isolation_level="kernel",
        )


def _profile_for(mode: SandboxMode, template: str) -> str:
    if mode == SandboxMode.READ_ONLY:
        return '(version 1)(deny default)(allow process-exec)(allow file-read*)'
    if template == "workspace-write":
        return '(version 1)(allow default)(deny network*)'
    return '(version 1)(allow default)'
