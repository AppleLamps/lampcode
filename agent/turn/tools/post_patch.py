"""Post-patch harness hooks (LSP diagnostics, tests)."""
from __future__ import annotations

from pathlib import Path

from agent.mcp.manager import McpManager

def run_lsp_diagnostics_after_patch(
    mcp_manager: McpManager,
    config: Config,
    path: str,
) -> str:
    tool_name = "mcp__lsp__lsp_diagnostics"
    if not mcp_manager.is_mcp_tool(tool_name):
        return ""
    output, exit_code, _err = mcp_manager.call_tool(
        tool_name,
        {"path": path},
        max_output=config.max_tool_output,
    )
    if exit_code != 0:
        return ""
    return output.strip()[:4000]


def run_post_patch_test(command: str, cwd: Path) -> str:
    import subprocess

    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return output.strip()[:4000] or f"(exit {proc.returncode}, no output)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"post_patch_test failed: {exc}"
