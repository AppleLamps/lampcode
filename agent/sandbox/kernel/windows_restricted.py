from __future__ import annotations

import sys

from agent.sandbox.kernel.base import KernelWrapResult
from agent.sandbox.kernel.policy import policy_label
from agent.sandbox.policy import SandboxMode


class WindowsRestrictedBackend:
    name = "windows_restricted"

    def __init__(self, settings) -> None:
        self._settings = settings

    def available(self) -> bool:
        return sys.platform == "win32"

    def wrap_command(
        self,
        *,
        cmd: str,
        cwd: str,
        sandbox_mode: str,
        settings,
    ) -> KernelWrapResult:
        mode = SandboxMode.from_str(sandbox_mode)
        meta = {
            "policy": policy_label(mode),
            "use_restricted_token": settings.windows.use_restricted_token,
            "job_object_memory_mb": settings.windows.job_object_memory_mb,
            "job_object_cpu_rate": settings.windows.job_object_cpu_rate,
            "allow_network": settings.windows.allow_network,
            "wrapper": "restricted_token+job_object",
        }
        # Execution wrapper applied at subprocess layer via meta flags
        return KernelWrapResult(
            applied=True,
            backend=self.name,
            wrapped_cmd=cmd,
            shell=True,
            meta=meta,
            isolation_level="kernel",
        )
