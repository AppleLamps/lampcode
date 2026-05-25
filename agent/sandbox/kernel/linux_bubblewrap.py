from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from agent.sandbox.kernel.base import KernelWrapResult
from agent.sandbox.kernel.policy import policy_label
from agent.sandbox.policy import SandboxMode


class LinuxBubblewrapBackend:
    name = "bubblewrap"

    def __init__(self, settings) -> None:
        self._settings = settings

    def available(self) -> bool:
        binary = self._settings.linux.bwrap_binary
        return shutil.which(binary) is not None

    def wrap_command(
        self,
        *,
        cmd: str,
        cwd: str,
        sandbox_mode: str,
        settings,
    ) -> KernelWrapResult:
        mode = SandboxMode.from_str(sandbox_mode)
        binary = settings.linux.bwrap_binary
        argv: list[str] = [
            binary,
            "--die-with-parent",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
        ]
        if not settings.linux.allow_network:
            argv.extend(["--unshare-net"])
        if settings.linux.unshare_user:
            argv.append("--unshare-user")
        for ro in settings.linux.ro_bind_paths:
            ro_path = str(Path(ro))
            if Path(ro_path).exists():
                argv.extend(["--ro-bind", ro_path, ro_path])
        cwd_path = str(Path(cwd).resolve())
        if mode == SandboxMode.READ_ONLY:
            argv.extend(["--ro-bind", cwd_path, cwd_path])
        else:
            argv.extend(["--bind", cwd_path, cwd_path])
        argv.extend(["--chdir", cwd_path, "/bin/sh", "-c", cmd])
        return KernelWrapResult(
            applied=True,
            backend=self.name,
            argv=argv,
            shell=False,
            meta={"policy": policy_label(mode), "argv_preview": argv[:8]},
            isolation_level="kernel",
        )
