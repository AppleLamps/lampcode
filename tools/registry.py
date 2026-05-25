from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.config import Config
from agent.models import CommandExecutionItem, FileChangeItem
from tools.files import read_file, write_file
from tools.search import search_repo
from tools.shell import run_command


@dataclass
class ToolSpec:
    name: str
    schema: dict[str, Any]
    handler: Callable[..., str]
    requires_approval: bool = False


def get_tool_schemas() -> list[dict[str, Any]]:
    return [spec.schema for spec in TOOL_REGISTRY.values()]


def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    config: Config,
) -> tuple[str, CommandExecutionItem | FileChangeItem | None]:
    """
    Dispatch a tool call. Returns (result_text, tracking_item_or_none).
    Tracking items are returned for tools that need persistence (run_command, write_file).
    read_file and search_repo execute immediately without tracking items.
    """
    if name not in TOOL_REGISTRY:
        return f"Unknown tool: {name}", None

    spec = TOOL_REGISTRY[name]

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
        return output, item

    if name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        item = FileChangeItem(path=path, status="pending")
        result = write_file(config.cwd, path, content)
        item.status = "completed" if result.startswith("Successfully") else "failed"
        item.summary = result
        return result, item

    if name == "read_file":
        result = read_file(
            config.cwd,
            arguments.get("path", ""),
            offset=arguments.get("offset"),
            limit=arguments.get("limit"),
        )
        return result, None

    if name == "search_repo":
        result = search_repo(
            config.cwd,
            arguments.get("pattern", ""),
            path=arguments.get("path"),
            glob=arguments.get("glob"),
            max_output=config.max_tool_output,
        )
        return result, None

    return spec.handler(**arguments), None


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
                        "cmd": {
                            "type": "string",
                            "description": "Shell command to execute.",
                        },
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
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file.",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "1-based line number to start reading.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of lines to read.",
                        },
                    },
                    "required": ["path"],
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
                "description": "Write or overwrite a file in the project directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full file content to write.",
                        },
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
                        "pattern": {
                            "type": "string",
                            "description": "Regex pattern to search for.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Optional subdirectory to search in.",
                        },
                        "glob": {
                            "type": "string",
                            "description": "Optional filename glob, e.g. *.py",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        handler=lambda **_: "",
    ),
}
