from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.config import Config
from agent.execution.docker_files import docker_file_tools_enabled
from agent.mcp.manager import McpManager
from agent.models import CommandExecutionItem, FileChangeItem, McpToolCallItem, WebSearchItem
from tools.code_intel import (
    file_imports,
    file_outline,
    find_references,
    go_to_definition,
)
from tools.files import read_file, write_file
from tools.git_commit import GIT_COMMIT_SCHEMA, git_commit
from tools.patch import APPLY_PATCH_PARAM_DESCRIPTION, apply_patch
from tools.search import search_repo
from tools.shell import run_command
from tools.web_search import format_results_for_model, web_search


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
    web_search_item: WebSearchItem | None = None
    isolation_meta: dict | None = None  # legacy alias
    execution_meta: dict | None = None
    file_tool_meta: dict | None = None


def get_tool_schemas(
    mcp_manager: McpManager | None = None,
    config: Config | None = None,
    *,
    allow_spawn: bool = False,
    allowed_tools: list[str] | None = None,
    allow_mcp_servers: list[str] | None = None,
) -> list[dict[str, Any]]:
    schemas = [spec.schema for spec in TOOL_REGISTRY.values()]
    if config:
        from agent.git import detect_repo_root

        if detect_repo_root(config.cwd):
            schemas.append(GIT_COMMIT_SCHEMA)
    if config and config.web_search.enabled:
        schemas.append(WEB_SEARCH_SCHEMA)
    if config and config.multi_agent.enabled and allow_spawn:
        from agent.multi_agent.tools import MULTI_AGENT_TOOL_SCHEMAS

        schemas.extend(MULTI_AGENT_TOOL_SCHEMAS)
    if mcp_manager:
        schemas.extend(mcp_manager.get_tool_schemas())
    if allowed_tools is not None or allow_mcp_servers:
        from agent.tool_access import filter_tool_schemas

        schemas = filter_tool_schemas(
            schemas,
            allowed_tools,
            allow_mcp_servers=allow_mcp_servers,
        )
    return schemas


def tool_requires_approval(
    name: str, mcp_manager: McpManager | None = None, config: Config | None = None
) -> bool:
    if name == "spawn_worker" or name == "spawn_worker_batch":
        return True
    if name in ("wait_workers", "list_workers", "get_worker_graph"):
        return False
    if name == "web_search":
        return True
    if name == "git_commit":
        return True
    if name in ("request_user_input", "request_permissions"):
        return True
    if mcp_manager and mcp_manager.is_mcp_tool(name):
        return mcp_manager.requires_approval(name)
    spec = TOOL_REGISTRY.get(name)
    return spec.requires_approval if spec else False


def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    config: Config,
    mcp_manager: McpManager | None = None,
    *,
    thread_id: str | None = None,
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

    if name == "web_search":
        if not config.web_search.enabled:
            return DispatchResult(text="Web search is disabled in configuration.")
        query = arguments.get("query", "")
        item = WebSearchItem(query=query, status="pending")
        results, error = web_search(query, config.web_search)
        if error:
            item.status = "failed"
            item.error = error
            return DispatchResult(
                text=f"Web search failed: {error}", web_search_item=item
            )
        item.results = results
        item.status = "completed"
        text = format_results_for_model(results)
        return DispatchResult(text=text, web_search_item=item)

    if name == "git_commit":
        message = arguments.get("message", "")
        all_files = bool(arguments.get("all", False))
        output = git_commit(message, all_files=all_files, cwd=str(config.cwd))
        item = CommandExecutionItem(
            command=f"git commit -m {message!r}" + (" (all)" if all_files else ""),
            cwd=str(config.cwd),
            status="completed" if "failed" not in output.lower() else "failed",
            output=output,
        )
        return DispatchResult(text=output, command_item=item)

    if name not in TOOL_REGISTRY:
        if name in ("request_user_input", "request_permissions"):
            return DispatchResult(text=f"{name} is handled by the harness loop")
        return DispatchResult(text=f"Unknown tool: {name}")

    if name == "run_command":
        cmd = arguments.get("cmd", "")
        workdir = arguments.get("workdir")
        item = CommandExecutionItem(
            command=cmd,
            cwd=str(config.cwd if not workdir else config.cwd / workdir),
            status="running",
        )
        output, exit_code, duration_ms, exec_meta = run_command(
            config.cwd,
            cmd,
            workdir=workdir,
            timeout=config.command_timeout,
            max_output=arguments.get("max_output_chars") or config.max_tool_output,
            config=config,
            thread_id=thread_id,
            session_id=arguments.get("session_id"),
            stdin=arguments.get("stdin"),
            new_session=bool(arguments.get("new_session")),
            yield_ms=arguments.get("yield_ms"),
            background=(
                None
                if arguments.get("background") is None
                else bool(arguments.get("background"))
            ),
        )
        item.output = output
        item.exit_code = exit_code
        item.duration_ms = duration_ms
        item.status = "completed" if exit_code == 0 else "failed"
        if exec_meta:
            backend = exec_meta.get("backend")
            if backend in ("local", "docker", "ssh"):
                item.backend = backend
            item.container_id = exec_meta.get("container_id")
            item.image = exec_meta.get("image")
            item.remote_host = exec_meta.get("remote_host")
            item.remote_user = exec_meta.get("remote_user")
            item.kernel_backend = exec_meta.get("kernel_backend")
            il = exec_meta.get("isolation_level")
            if il in ("heuristic", "profile", "kernel"):
                item.isolation_level = il
        return DispatchResult(
            text=output,
            command_item=item,
            execution_meta=exec_meta,
            isolation_meta=exec_meta,
        )

    if name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        item = FileChangeItem(path=path, status="pending", change_type="overwrite")
        file_tool_meta = None
        if docker_file_tools_enabled(config):
            from agent.execution.docker_files import docker_write_file

            result, file_tool_meta = docker_write_file(config.cwd, path, content, config)
            file_tool_meta["tool_name"] = "write_file"
        else:
            result = write_file(config.cwd, path, content)
        item.status = "completed" if result.startswith("Successfully") else "failed"
        item.summary = result
        item.content = content
        return DispatchResult(
            text=result, file_items=[item], file_tool_meta=file_tool_meta
        )

    if name == "apply_patch":
        patch_text = arguments.get("patch", "")
        file_tool_meta = None
        if docker_file_tools_enabled(config):
            from agent.execution.docker_files import docker_apply_patch

            outcome, file_tool_meta = docker_apply_patch(config.cwd, patch_text, config)
            file_tool_meta["tool_name"] = "apply_patch"
        else:
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
        return DispatchResult(text="\n".join(lines), file_items=file_items, file_tool_meta=file_tool_meta)

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

    if name == "file_outline":
        return DispatchResult(
            text=file_outline(config.cwd, arguments.get("path", "")),
        )

    if name == "go_to_definition":
        return DispatchResult(
            text=go_to_definition(
                config.cwd,
                arguments.get("symbol", ""),
                path_hint=arguments.get("path_hint"),
                max_results=int(arguments.get("max_results", 15)),
            ),
        )

    if name == "find_references":
        return DispatchResult(
            text=find_references(
                config.cwd,
                arguments.get("symbol", ""),
                path=arguments.get("path"),
                glob=arguments.get("glob"),
                max_results=int(arguments.get("max_results", 80)),
                max_output=config.max_tool_output,
                prefer_ripgrep=config.prefer_ripgrep,
            ),
        )

    if name == "file_imports":
        return DispatchResult(
            text=file_imports(
                config.cwd,
                arguments.get("path", ""),
                max_results=int(arguments.get("max_results", 40)),
            ),
        )

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
                        "session_id": {
                            "type": "string",
                            "description": "Persistent shell session id (when [shell] enabled).",
                        },
                        "stdin": {
                            "type": "string",
                            "description": "Optional stdin for persistent shell session.",
                        },
                        "new_session": {
                            "type": "boolean",
                            "description": "Start a new persistent shell session.",
                        },
                        "max_output_chars": {
                            "type": "integer",
                            "description": "Max characters of command output to return.",
                        },
                        "yield_ms": {
                            "type": "integer",
                            "description": "Max wait time (ms) before returning partial persistent-shell output.",
                        },
                        "background": {
                            "type": "boolean",
                            "description": (
                                "Force detached start (true) or foreground wait (false). "
                                "Omit to auto-detect dev servers (http.server, npm run dev, vite, …)."
                            ),
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
                            "description": APPLY_PATCH_PARAM_DESCRIPTION,
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
    "file_outline": ToolSpec(
        name="file_outline",
        requires_approval=False,
        schema={
            "type": "function",
            "function": {
                "name": "file_outline",
                "description": (
                    "List functions, classes, and top-level symbols in a file "
                    "(Python AST; regex for JS/TS/Go/Rust)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to project root.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "go_to_definition": ToolSpec(
        name="go_to_definition",
        requires_approval=False,
        schema={
            "type": "function",
            "function": {
                "name": "go_to_definition",
                "description": (
                    "Find where a symbol is defined. Optional path_hint searches that file first "
                    "(AST for Python), then the repo."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "path_hint": {
                            "type": "string",
                            "description": "Optional file likely containing the definition.",
                        },
                        "max_results": {"type": "integer"},
                    },
                    "required": ["symbol"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "find_references": ToolSpec(
        name="find_references",
        requires_approval=False,
        schema={
            "type": "function",
            "function": {
                "name": "find_references",
                "description": (
                    "Find references to a symbol across the repo (word-boundary search). "
                    "Prefer over raw search_repo for identifiers."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "path": {
                            "type": "string",
                            "description": "Optional subdirectory or file to limit search.",
                        },
                        "glob": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["symbol"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "file_imports": ToolSpec(
        name="file_imports",
        requires_approval=False,
        schema={
            "type": "function",
            "function": {
                "name": "file_imports",
                "description": (
                    "List imports in a file and resolve project-local targets when possible "
                    "(Python/JS/TS). Use before refactors to see dependency impact."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        handler=lambda **_: "",
    ),
    "request_user_input": ToolSpec(
        name="request_user_input",
        requires_approval=True,
        schema={
            "type": "function",
            "function": {
                "name": "request_user_input",
                "description": "Ask the user a structured question mid-turn.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "questions": {
                            "type": "array",
                            "description": "Optional batch of questions (each may have options).",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string"},
                                    "options": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "allow_free_text": {"type": "boolean"},
                                },
                                "required": ["question"],
                            },
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional multiple-choice options.",
                        },
                        "allow_free_text": {
                            "type": "boolean",
                            "description": "Allow free-text answer when options provided.",
                        },
                    },
                },
            },
        },
        handler=lambda **_: "",
    ),
    "request_permissions": ToolSpec(
        name="request_permissions",
        requires_approval=True,
        schema={
            "type": "function",
            "function": {
                "name": "request_permissions",
                "description": "Request temporary sandbox permission escalation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["network", "write_outside_cwd", "full_access"],
                        },
                        "reason": {"type": "string"},
                        "duration": {
                            "type": "string",
                            "enum": ["turn", "session"],
                            "description": "Escalation duration scope.",
                        },
                    },
                    "required": ["scope", "reason"],
                },
            },
        },
        handler=lambda **_: "",
    ),
}

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the public web for documentation, errors, or references.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
            },
            "required": ["query"],
        },
    },
}
