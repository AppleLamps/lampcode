from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.config import Config
from agent.mcp.manager import McpManager
from agent.models import CommandExecutionItem, FileChangeItem, McpToolCallItem
from tools.files import read_file, write_file
from tools.patch import apply_patch
from tools.search import search_repo
from tools.shell import run_command


@dataclass
class ToolSpec:
    name: str
    schema: dict[str, Any]
    handler: Callable[..., str]
    requires_approval: bool = False


@dataclass
class DispatchResult:
    text: str
    command_item: CommandExecutionItem | None = None
    file_items: list[FileChangeItem] = field(default_factory=list)
    mcp_item: McpToolCallItem | None = None


def get_tool_schemas(mcp_manager: McpManager | None = None) -> list[dict[str, Any]]:
    schemas = [spec.schema for spec in TOOL_REGISTRY.values()]
    if mcp_manager:
        schemas.extend(mcp_manager.get_tool_schemas())
    return schemas


def tool_requires_approval(
    name: str, mcp_manager: McpManager | None = None
) -> bool:
    if mcp_manager and mcp_manager.is_mcp_tool(name):
        return mcp_manager.requires_approval(name)
    spec = TOOL_REGISTRY.get(name)
    return spec.requires_approval if spec else False


def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    config: Config,
    mcp_manager: McpManager | None = None,
) -> DispatchResult:
    if mcp_manager and mcp_manager.is_mcp_tool(name):
        output, exit_code, error = mcp_manager.call_tool(
            name, arguments, max_output=config.max_tool_output
        )
        ref = mcp_manager.tool_map.get(name)
        item = McpToolCallItem(
            server=ref.server if ref else "unknown",
            tool=ref.tool if ref else name,
            arguments=arguments,
            status="completed" if exit_code == 0 else "failed",
            output=output,
            error=error,
        )
        return DispatchResult(text=output, mcp_item=item)

    if name not in TOOL_REGISTRY:
        return DispatchResult(text=f"Unknown tool: {name}")

    if name == "run_command":
        cmd = arguments.get("cmd", "")
        workdir = arguments.get("workdir")
        item = CommandExecutionItem(
            command=cmd,
            cwd=str(config.cwd if not workdir else config.cwd / workdir),
            status="running",
        )
        output, exit_code, duration_ms = run_command(
            config.cwd,
            cmd,
            workdir=workdir,
            timeout=config.command_timeout,
            max_output=config.max_tool_output,
        )
        item.output = output
        item.exit_code = exit_code
        item.duration_ms = duration_ms
        item.status = "completed" if exit_code == 0 else "failed"
        return DispatchResult(text=output, command_item=item)

    if name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        item = FileChangeItem(path=path, status="pending", change_type="overwrite")
        result = write_file(config.cwd, path, content)
        item.status = "completed" if result.startswith("Successfully") else "failed"
        item.summary = result
        item.content = content
        return DispatchResult(text=result, file_items=[item])

    if name == "apply_patch":
        patch_text = arguments.get("patch", "")
        outcome = apply_patch(config.cwd, patch_text)
        if not outcome.ok:
            hint = " Use read_file to inspect the file, then retry with a corrected patch."
            return DispatchResult(text=f"Patch failed: {outcome.error}{hint}")
        file_items: list[FileChangeItem] = []
        lines: list[str] = []
        for pr in outcome.results:
            item = FileChangeItem(
                path=pr.path,
                status="completed",
                change_type=pr.change_type,  # type: ignore[arg-type]
                summary=pr.summary,
                diff_snippet=pr.diff_snippet,
            )
            file_items.append(item)
            lines.append(f"{pr.path}: {pr.summary}")
        return DispatchResult(text="\n".join(lines), file_items=file_items)

    if name == "read_file":
        result = read_file(
            config.cwd,
            arguments.get("path", ""),
            offset=arguments.get("offset"),
            limit=arguments.get("limit"),
        )
        return DispatchResult(text=result)

    if name == "search_repo":
        result = search_repo(
            config.cwd,
            arguments.get("pattern", ""),
            path=arguments.get("path"),
            glob=arguments.get("glob"),
            max_output=config.max_tool_output,
            prefer_ripgrep=config.prefer_ripgrep,
        )
        return DispatchResult(text=result)

    return DispatchResult(text=TOOL_REGISTRY[name].handler(**arguments))


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": raw}


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "run_command": ToolSpec(
        name="run_command",
        requires_approval=True,
        schema={
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a shell command in the project directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "Shell command."},
                        "workdir": {
                            "type": "string",
                            "description": "Optional subdirectory relative to project root.",
                        },
                    },
                    "required": ["cmd"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "read_file": ToolSpec(
        name="read_file",
        requires_approval=False,
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the project directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "apply_patch": ToolSpec(
        name="apply_patch",
        requires_approval=True,
        schema={
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": (
                    "Apply a structured patch to modify, add, or delete files. "
                    "Prefer this over write_file for edits to existing files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": (
                                "Patch text using *** Begin Patch / *** End Patch format."
                            ),
                        },
                    },
                    "required": ["patch"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "write_file": ToolSpec(
        name="write_file",
        requires_approval=True,
        schema={
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write or overwrite an entire file. Prefer apply_patch for edits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "search_repo": ToolSpec(
        name="search_repo",
        requires_approval=False,
        schema={
            "type": "function",
            "function": {
                "name": "search_repo",
                "description": "Search for a regex pattern in the repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        handler=lambda **_: "",
    ),
}
