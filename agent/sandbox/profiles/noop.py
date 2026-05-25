from __future__ import annotations

from agent.sandbox.profiles.base import SandboxProfileBase


class NoopProfile(SandboxProfileBase):
    name = "noop"

    def apply(self, *, cmd: str, cwd: str, settings):
        from agent.sandbox.profiles import ProfileResult

        return ProfileResult(applied=False, profile=self.name, reason="noop profile")
