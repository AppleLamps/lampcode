from __future__ import annotations

import sys
from typing import Any

from agent.sandbox.kernel.factory import select_kernel_backend
from agent.sandbox.kernel.windows_appcontainer import probe_appcontainer


def probe_capabilities(settings) -> dict[str, Any]:
    backend = select_kernel_backend(settings)
    result: dict[str, Any] = {
        "backend": backend.name if backend else "none",
        "available": backend.available() if backend else False,
        "platform": sys.platform,
        "selected": settings.backend,
    }
    if sys.platform == "win32":
        ac = probe_appcontainer()
        result["appcontainer"] = ac["appcontainer"]
        result["appcontainer_reason"] = ac.get("reason", "")
        result["windows_build"] = ac.get("build", 0)
    return result

