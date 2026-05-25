from __future__ import annotations

import os
import sys
from typing import Any

from agent.metrics import MetricsCollector
from agent.sandbox.kernel.base import KernelWrapResult
from agent.sandbox.kernel.policy import policy_label
from agent.sandbox.policy import SandboxMode

# Test hooks — set via monkeypatch in unit tests.
_force_available: bool | None = None
_force_unavailable_reason: str = ""


def reset_appcontainer_probe_for_tests() -> None:
    global _force_available, _force_unavailable_reason
    _force_available = None
    _force_unavailable_reason = ""


def _win32_build() -> int:
    if sys.platform != "win32":
        return 0
    try:
        version = sys.getwindowsversion()  # type: ignore[attr-defined]
        return version.build
    except AttributeError:
        return 0


def _check_win32_appcontainer_api() -> tuple[bool, str]:
    if _force_available is not None:
        if _force_available:
            return True, ""
        return False, _force_unavailable_reason or "forced unavailable"
    if sys.platform != "win32":
        return False, "not Windows"
    build = _win32_build()
    if build < 10240:
        return False, f"Windows build {build} too old (need Win10+)"
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if not hasattr(kernel32, "CreateProcessW"):
            return False, "CreateProcessW unavailable"
    except Exception as exc:
        return False, str(exc)
    if os.environ.get("AGENT_APPCONTAINER_DISABLED") == "1":
        return False, "AGENT_APPCONTAINER_DISABLED=1"
    return True, ""


def probe_appcontainer() -> dict[str, Any]:
    ok, reason = _check_win32_appcontainer_api()
    return {
        "appcontainer": "available" if ok else "unavailable",
        "reason": reason,
        "platform": sys.platform,
        "build": _win32_build(),
    }


class WindowsAppContainerBackend:
    name = "windows_appcontainer"

    def __init__(self, settings) -> None:
        self._settings = settings

    def available(self) -> bool:
        ok, _ = _check_win32_appcontainer_api()
        return ok

    def unavailable_reason(self) -> str:
        _, reason = _check_win32_appcontainer_api()
        return reason

    def wrap_command(
        self,
        *,
        cmd: str,
        cwd: str,
        sandbox_mode: str,
        settings,
    ) -> KernelWrapResult:
        mode = SandboxMode.from_str(sandbox_mode)
        win = settings.windows
        read_only = mode == SandboxMode.READ_ONLY
        meta: dict[str, Any] = {
            "policy": policy_label(mode),
            "wrapper": "windows_appcontainer",
            "allow_network": win.allow_network,
            "workspace_cap": win.workspace_cap,
            "capability_sids": list(win.capability_sids),
            "read_only": read_only,
            "workspace_root": cwd,
            "launch_mode": "appcontainer",
        }
        MetricsCollector.global_collector().inc_labeled("agent_sandbox_appcontainer_total", "wrap")
        return KernelWrapResult(
            applied=True,
            backend=self.name,
            wrapped_cmd=cmd,
            shell=True,
            meta=meta,
            isolation_level="kernel",
        )


def apply_appcontainer_meta_to_result(meta: dict[str, Any], result_meta: dict[str, Any]) -> None:
    if meta.get("wrapper") == "windows_appcontainer":
        result_meta["kernel_backend"] = "windows_appcontainer"
        result_meta["commandExecution"] = {"kernel_backend": "windows_appcontainer"}
