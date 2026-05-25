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

    if emitter:
        emitter.sandbox_kernel_selected(
            thread_id, turn_id, backend=backend.name, mode=sandbox_mode.value
        )

    if not backend.available():
        return _fallback(settings, f"{backend.name} unavailable", emitter, thread_id, turn_id)

    try:
        result = backend.wrap_command(
            cmd=cmd,
            cwd=cwd,
            sandbox_mode=sandbox_mode.value,
            settings=settings,
        )
    except Exception as exc:
        return _fallback(settings, str(exc), emitter, thread_id, turn_id)

    if result.applied:
        MetricsCollector.global_collector().inc_labeled(
            "agent_sandbox_kernel_total", f"{result.backend}:applied"
        )
        if emitter:
            emitter.sandbox_kernel_applied(
                thread_id, turn_id, backend=result.backend, mode=sandbox_mode.value
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
