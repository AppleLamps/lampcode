from __future__ import annotations

import sys

from agent.sandbox.kernel.base import KernelSandboxBackend
from agent.sandbox.kernel.linux_bubblewrap import LinuxBubblewrapBackend
from agent.sandbox.kernel.macos_seatbelt import MacosSeatbeltBackend
from agent.sandbox.kernel.windows_restricted import WindowsRestrictedBackend


def select_kernel_backend(settings) -> KernelSandboxBackend | None:
    choice = (settings.backend or "auto").lower()
    if choice == "none":
        return None
    if choice == "bubblewrap":
        return LinuxBubblewrapBackend(settings)
    if choice == "seatbelt":
        return MacosSeatbeltBackend(settings)
    if choice == "windows_restricted":
        return WindowsRestrictedBackend(settings)
    if choice == "auto":
        if sys.platform == "win32":
            return WindowsRestrictedBackend(settings)
        if sys.platform == "darwin":
            return MacosSeatbeltBackend(settings)
        if sys.platform.startswith("linux"):
            return LinuxBubblewrapBackend(settings)
    return None
