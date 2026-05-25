from __future__ import annotations

import sys

from agent.sandbox.profiles.base import SandboxProfileBase


class WindowsJobProfile(SandboxProfileBase):
    name = "windows_job"

    def __init__(self, settings) -> None:
        self.settings = settings

    def available(self) -> bool:
        return sys.platform == "win32"

    def apply(self, *, cmd: str, cwd: str, settings):
        from agent.sandbox.profiles import ProfileResult

        if not self.available():
            return ProfileResult(
                applied=False,
                profile=self.name,
                reason="windows_job only available on Windows",
            )
        mem_mb = settings.windows_job_memory_limit_mb
        cpu_rate = settings.windows_job_cpu_rate
        return ProfileResult(
            applied=True,
            profile=self.name,
            meta={
                "memory_limit_mb": mem_mb,
                "cpu_rate": cpu_rate,
                "note": "experimental Job Object limits (best-effort)",
            },
        )
