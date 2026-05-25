from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.metrics import MetricsCollector
from agent.sandbox.kernel.base import KernelWrapResult
from agent.sandbox.kernel.factory import select_kernel_backend
from agent.sandbox.kernel.policy import kernel_enabled_for_mode
from agent.sandbox.policy import SandboxMode


@dataclass
class KernelLinuxSettings:
    bwrap_binary: str = "bwrap"
    unshare_user: bool = False
    ro_bind_paths: list[str] = field(default_factory=lambda: ["/usr", "/lib", "/bin"])
    allow_network: bool = False


@dataclass
class KernelMacosSettings:
    sandbox_exec: str = "/usr/bin/sandbox-exec"
    profile_template: str = "workspace-write"


@dataclass
class KernelWindowsSettings:
    use_restricted_token: bool = True
    job_object_memory_mb: int = 1024
    job_object_cpu_rate: int = 50
    allow_network: bool = False
    backend_preference: str = "appcontainer_then_restricted"
    capability_sids: list[str] = field(default_factory=list)
    workspace_cap: bool = True


@dataclass
class KernelSandboxSettings:
    enabled: bool = False
    backend: str = "auto"
    fail_open: bool = True
    apply_to: list[str] = field(default_factory=lambda: ["run_command"])
    linux: KernelLinuxSettings = field(default_factory=KernelLinuxSettings)
    macos: KernelMacosSettings = field(default_factory=KernelMacosSettings)
    windows: KernelWindowsSettings = field(default_factory=KernelWindowsSettings)


def apply_kernel_sandbox(
    config,
    *,
    cmd: str,
    cwd: str,
    tool: str = "run_command",
    emitter=None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> KernelWrapResult:
    settings = config.sandbox_kernel
    sandbox_mode = config.sandbox_mode

    if tool not in settings.apply_to:
        return KernelWrapResult(applied=False, backend="skipped", reason="tool not in apply_to")

    if not kernel_enabled_for_mode(sandbox_mode, kernel_enabled=settings.enabled):
        return KernelWrapResult(
            applied=False,
            backend="disabled",
            reason="kernel sandbox disabled for mode",
        )

    backend = select_kernel_backend(settings)
    if backend is None:
        return _fallback(settings, "no backend selected", emitter, thread_id, turn_id)

    import sys as _sys

    if _sys.platform == "win32":
        pref = (settings.windows.backend_preference or "").lower()
        if pref in ("appcontainer_then_restricted", "appcontainer") and backend.name == "windows_restricted":
            from agent.sandbox.kernel.windows_appcontainer import WindowsAppContainerBackend

            ac = WindowsAppContainerBackend(settings)
            if not ac.available():
                MetricsCollector.global_collector().inc_labeled(
                    "agent_sandbox_appcontainer_total", "fallback"
                )
                if emitter:
                    emitter.sandbox_kernel_fallback(
                        thread_id,
                        turn_id,
                        reason=ac.unavailable_reason() or "appcontainer unavailable",
                        fallback_to="windows_restricted",
                    )

    if emitter:
        emitter.sandbox_kernel_selected(
            thread_id, turn_id, backend=backend.name, mode=sandbox_mode.value
        )

    if not backend.available():
        fb = _try_windows_restricted_fallback(
            settings, backend, sandbox_mode, cmd, cwd, emitter, thread_id, turn_id
        )
        if fb is not None:
            return fb
        return _fallback(settings, f"{backend.name} unavailable", emitter, thread_id, turn_id)

    try:
        result = backend.wrap_command(
            cmd=cmd,
            cwd=cwd,
            sandbox_mode=sandbox_mode.value,
            settings=settings,
        )
    except Exception as exc:
        fb = _try_windows_restricted_fallback(
            settings, backend, sandbox_mode, cmd, cwd, emitter, thread_id, turn_id, reason=str(exc)
        )
        if fb is not None:
            return fb
        return _fallback(settings, str(exc), emitter, thread_id, turn_id)

    if result.applied:
        if result.backend == "windows_appcontainer":
            MetricsCollector.global_collector().inc_labeled(
                "agent_sandbox_appcontainer_total", "applied"
            )
        MetricsCollector.global_collector().inc_labeled(
            "agent_sandbox_kernel_total", f"{result.backend}:applied"
        )
        if emitter:
            emitter.sandbox_kernel_applied(
                thread_id, turn_id, backend=result.backend, mode=sandbox_mode.value
            )
    return result


def _try_windows_restricted_fallback(
    settings,
    backend,
    sandbox_mode,
    cmd: str,
    cwd: str,
    emitter,
    thread_id: str | None,
    turn_id: str | None,
    *,
    reason: str = "",
) -> KernelWrapResult | None:
    import sys

    if sys.platform != "win32":
        return None
    pref = (settings.windows.backend_preference or "").lower()
    if backend.name != "windows_appcontainer" and pref != "appcontainer":
        return None
    if pref not in ("appcontainer_then_restricted", "appcontainer"):
        return None
    from agent.sandbox.kernel.windows_restricted import WindowsRestrictedBackend

    restricted = WindowsRestrictedBackend(settings)
    if not restricted.available():
        return None
    fb_reason = reason or backend.unavailable_reason() if hasattr(backend, "unavailable_reason") else reason
    MetricsCollector.global_collector().inc_labeled("agent_sandbox_appcontainer_total", "fallback")
    MetricsCollector.global_collector().inc_labeled("agent_sandbox_kernel_total", "fallback")
    if emitter:
        emitter.sandbox_kernel_fallback(
            thread_id, turn_id, reason=fb_reason or "appcontainer unavailable", fallback_to="windows_restricted"
        )
    result = restricted.wrap_command(
        cmd=cmd,
        cwd=cwd,
        sandbox_mode=sandbox_mode.value if hasattr(sandbox_mode, "value") else str(sandbox_mode),
        settings=settings,
    )
    return result


def _fallback(
    settings,
    reason: str,
    emitter,
    thread_id: str | None,
    turn_id: str | None,
) -> KernelWrapResult:
    MetricsCollector.global_collector().inc_labeled(
        "agent_sandbox_kernel_total", "fallback"
    )
    if emitter:
        emitter.sandbox_kernel_fallback(thread_id, turn_id, reason=reason)
    if settings.fail_open:
        return KernelWrapResult(applied=False, backend="fallback", reason=reason)
    raise RuntimeError(f"Kernel sandbox unavailable: {reason}")


from agent.sandbox.kernel.doctor import probe_capabilities

__all__ = [
    "KernelSandboxSettings",
    "KernelLinuxSettings",
    "KernelMacosSettings",
    "KernelWindowsSettings",
    "apply_kernel_sandbox",
    "probe_capabilities",
]
