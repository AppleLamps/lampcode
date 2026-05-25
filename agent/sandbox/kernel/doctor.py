from __future__ import annotations

import sys
from typing import Any

from agent.sandbox.kernel.factory import select_kernel_backend


def probe_capabilities(settings) -> dict[str, Any]:
    backend = select_kernel_backend(settings)
    if backend is None:
        return {"backend": "none", "available": False, "platform": sys.platform}
    return {
        "backend": backend.name,
        "available": backend.available(),
        "platform": sys.platform,
        "selected": settings.backend,
    }
