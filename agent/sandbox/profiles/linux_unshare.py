from __future__ import annotations

import shutil
import sys

from agent.sandbox.profiles.base import SandboxProfileBase


class LinuxUnshareProfile(SandboxProfileBase):
    name = "linux_unshare"

    def __init__(self, settings) -> None:
        self.settings = settings

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("unshare") is not None

    def apply(self, *, cmd: str, cwd: str, settings):
        from agent.sandbox.profiles import ProfileResult

        if not self.available():
            return ProfileResult(
                applied=False,
                profile=self.name,
                reason="unshare binary not found",
            )
        flags = ["--fork"]
        if settings.linux_unshare_user:
            flags.append("--user")
        return ProfileResult(
            applied=True,
            profile=self.name,
            meta={"wrapper": f"unshare {' '.join(flags)}", "cmd": cmd[:120]},
        )
