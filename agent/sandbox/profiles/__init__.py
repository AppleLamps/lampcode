from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class ProfileResult:
    applied: bool
    profile: str
    reason: str = ""
    meta: dict[str, Any] | None = None


class SandboxProfile:
    name: str = "noop"

    def available(self) -> bool:
        return True

    def apply(self, *, cmd: str, cwd: str, settings) -> ProfileResult:
        return ProfileResult(applied=False, profile=self.name, reason="noop")


def select_profile(settings) -> SandboxProfile:
    from agent.sandbox.profiles.linux_unshare import LinuxUnshareProfile
    from agent.sandbox.profiles.noop import NoopProfile
    from agent.sandbox.profiles.windows_job import WindowsJobProfile

    choice = (settings.profile or "auto").lower()
    if choice == "noop":
        return NoopProfile()
    if choice == "windows_job":
        return WindowsJobProfile(settings)
    if choice == "linux_unshare":
        return LinuxUnshareProfile(settings)
    if choice == "auto":
        if sys.platform == "win32":
            return WindowsJobProfile(settings)
        if sys.platform.startswith("linux"):
            return LinuxUnshareProfile(settings)
    return NoopProfile()


def apply_sandbox_profile(
    config,
    *,
    cmd: str,
    cwd: str,
    emitter=None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> ProfileResult:
    settings = config.sandbox_profiles
    if not settings.enabled:
        return ProfileResult(applied=False, profile="disabled", reason="profiles disabled")
    if config.execution.backend != "local":
        return ProfileResult(applied=False, profile="skipped", reason="non-local backend")

    profile = select_profile(settings)
    if not profile.available():
        result = ProfileResult(
            applied=False,
            profile=profile.name,
            reason=f"{profile.name} unavailable on this platform",
        )
        if settings.fail_open:
            if emitter:
                emitter.sandbox_profile_skipped(
                    thread_id, turn_id, reason=result.reason, profile=profile.name
                )
            return result
        raise RuntimeError(result.reason)

    result = profile.apply(cmd=cmd, cwd=cwd, settings=settings)
    if emitter:
        if result.applied:
            emitter.sandbox_profile_applied(
                thread_id, turn_id, profile=result.profile, **(result.meta or {})
            )
        else:
            emitter.sandbox_profile_skipped(
                thread_id, turn_id, reason=result.reason or "not applied", profile=result.profile
            )
    return result
