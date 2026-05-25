from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any


@dataclass
class LspProbeResult:
    available: bool
    server: str = ""
    reason: str = ""


def probe_python_lsp() -> LspProbeResult:
    for cmd, name in (("pyright-langserver", "pyright"), ("pylsp", "pylsp")):
        if shutil.which(cmd):
            return LspProbeResult(available=True, server=name)
    return LspProbeResult(available=False, reason="Install pyright-langserver or python-lsp-server")


def fetch_completions_stub(*, path: str, line: int, col: int) -> list[dict[str, Any]]:
    """Stub — full stdio LSP client deferred; doctor probes availability only."""
    probe = probe_python_lsp()
    if not probe.available:
        return []
    return [{"label": "pass", "kind": "keyword", "detail": f"stub ({probe.server})"}]
