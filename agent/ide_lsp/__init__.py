from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.lsp.client import WorkspaceLspRouter
from agent.lsp.servers import LspProbeResult, probe_python_lsp, probe_typescript_lsp

__all__ = [
    "LspProbeResult",
    "probe_python_lsp",
    "probe_typescript_lsp",
    "fetch_completions",
]


def fetch_completions(
    *,
    path: str,
    line: int,
    col: int,
    cwd: Path | None = None,
) -> list[dict[str, Any]]:
    """IDE completions via shared LSP client (Pyright / typescript-language-server)."""
    workspace = (cwd or Path.cwd()).resolve()
    rel = path.replace("\\", "/")
    router = WorkspaceLspRouter(workspace=workspace)
    try:
        resolved = router.resolve_path(rel)
        text = router.read_file_text(resolved)
        session = router._session_for_path(resolved)
        items = session.completion(resolved, text, line=line, character=col)
        if not items:
            return []
        out: list[dict[str, Any]] = []
        for item in items[:40]:
            if isinstance(item, str):
                out.append({"label": item, "kind": "text"})
            elif isinstance(item, dict):
                out.append(
                    {
                        "label": item.get("label", ""),
                        "kind": str(item.get("kind", "text")),
                        "detail": item.get("detail", ""),
                    }
                )
        return out
    except Exception:
        return []
    finally:
        router.close_all()
