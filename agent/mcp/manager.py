from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.mcp.adapter import (
    exposed_tool_name,
    mcp_tool_to_openrouter,
    parse_exposed_tool_name,
    truncate_mcp_output,
)
from agent.settings import McpConfig, McpServerConfig


@dataclass
class McpToolRef:
    server: str
    tool: str
    exposed_name: str
    require_approval: bool
    schema: dict[str, Any]


@dataclass
class ConnectedServer:
    name: str
    session: Any
    tools: list[McpToolRef] = field(default_factory=list)
    read_stream: Any = None
    write_stream: Any = None
    transport_cm: Any = None


class McpManager:
    def __init__(
        self,
        config: McpConfig,
        *,
        session_factory: Callable | None = None,
    ) -> None:
        self.config = config
        self._session_factory = session_factory
        self._servers: dict[str, ConnectedServer] = {}
        self._tool_map: dict[str, McpToolRef] = {}
        self._loop = asyncio.new_event_loop()

    @property
    def tool_map(self) -> dict[str, McpToolRef]:
        return dict(self._tool_map)

    def connect_all(
        self,
        on_connected: Callable[[str], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
    ) -> None:
        self._loop.run_until_complete(self._connect_all_async(on_connected, on_failed))

    async def _connect_all_async(
        self,
        on_connected: Callable[[str], None] | None,
        on_failed: Callable[[str, str], None] | None,
    ) -> None:
        for name, server_cfg in self.config.servers.items():
            if not server_cfg.enabled:
                continue
            try:
                await self._connect_server(server_cfg)
                if on_connected:
                    on_connected(name)
            except Exception as exc:
                if on_failed:
                    on_failed(name, str(exc))

    async def _connect_server(self, cfg: McpServerConfig) -> None:
        if self._session_factory:
            session, tools = await self._session_factory(cfg)
            connected = ConnectedServer(name=cfg.name, session=session)
        else:
            session, tools, connected = await self._real_connect(cfg)

        self._servers[cfg.name] = connected
        refs: list[McpToolRef] = []
        for tool in tools:
            exposed = exposed_tool_name(
                cfg.name, tool["name"], use_prefix=self.config.settings.tool_name_prefix
            )
            ref = McpToolRef(
                server=cfg.name,
                tool=tool["name"],
                exposed_name=exposed,
                require_approval=cfg.require_approval,
                schema=mcp_tool_to_openrouter(
                    cfg.name,
                    tool["name"],
                    tool.get("description", ""),
                    tool.get("inputSchema", {}),
                    use_prefix=self.config.settings.tool_name_prefix,
                ),
            )
            refs.append(ref)
            self._tool_map[exposed] = ref

        connected.tools = refs
        self._servers[cfg.name] = connected

    async def _real_connect(self, cfg: McpServerConfig) -> tuple[Any, list[dict[str, Any]], ConnectedServer]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        args, env, cwd = self._stdio_launch_options(cfg)
        params_kwargs: dict[str, Any] = {
            "command": cfg.command,
            "args": args,
            "env": env or None,
        }
        if cwd and self._stdio_parameters_accept_cwd(StdioServerParameters):
            params_kwargs["cwd"] = str(cwd)
        params = StdioServerParameters(**params_kwargs)
        timeout = self.config.settings.startup_timeout_sec
        transport = stdio_client(params)
        read, write = await asyncio.wait_for(transport.__aenter__(), timeout=timeout)
        session = ClientSession(read, write)
        await asyncio.wait_for(session.__aenter__(), timeout=timeout)
        await asyncio.wait_for(session.initialize(), timeout=timeout)
        listed = await asyncio.wait_for(session.list_tools(), timeout=timeout)
        tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema or {"type": "object", "properties": {}},
            }
            for t in listed.tools
        ]
        connected = ConnectedServer(name=cfg.name, session=session, tools=[])
        connected.transport_cm = transport
        connected.read_stream = read
        connected.write_stream = write
        return session, tools, connected

    def _stdio_launch_options(
        self, cfg: McpServerConfig
    ) -> tuple[list[str], dict[str, str], Path | None]:
        args = list(cfg.args)
        env = dict(cfg.env)
        project_cwd = getattr(self.config, "project_cwd", None)
        launch_cwd: Path | None = None
        if project_cwd and self._is_lsp_mcp_server(cfg):
            launch_cwd = project_cwd
            env = {**os.environ, **env}
            workspace = self._normalize_workspace_arg(args, project_cwd)
            if workspace is None:
                workspace = project_cwd
                args.extend(["--workspace", str(project_cwd)])
            env["WORKSPACE_ROOT"] = str(workspace)
        return args, env, launch_cwd

    @staticmethod
    def _is_lsp_mcp_server(cfg: McpServerConfig) -> bool:
        command_name = Path(cfg.command).name
        return command_name == "lsp-mcp" or "lsp-mcp" in cfg.args

    @staticmethod
    def _normalize_workspace_arg(args: list[str], cwd: Path) -> Path | None:
        for index, arg in enumerate(args):
            if arg.startswith("--workspace="):
                value = arg.split("=", 1)[1]
                workspace = McpManager._resolve_workspace_value(value, cwd)
                args[index] = f"--workspace={workspace}"
                return workspace
            if arg in {"--workspace", "-w"} and index + 1 < len(args):
                workspace = McpManager._resolve_workspace_value(args[index + 1], cwd)
                args[index + 1] = str(workspace)
                return workspace
        return None

    @staticmethod
    def _resolve_workspace_value(value: str, cwd: Path) -> Path:
        workspace = Path(value)
        if not workspace.is_absolute():
            workspace = cwd / workspace
        return workspace.resolve()

    @staticmethod
    def _stdio_parameters_accept_cwd(params_type: Any) -> bool:
        fields = getattr(params_type, "model_fields", None)
        if isinstance(fields, dict):
            return "cwd" in fields
        annotations = getattr(params_type, "__annotations__", {})
        return isinstance(annotations, dict) and "cwd" in annotations

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [ref.schema for ref in self._tool_map.values()]

    def call_tool(
        self,
        exposed_name: str,
        arguments: dict[str, Any],
        *,
        max_output: int = 20_000,
    ) -> tuple[str, int, str | None]:
        return self._loop.run_until_complete(
            self._call_tool_async(exposed_name, arguments, max_output=max_output)
        )

    async def _call_tool_async(
        self,
        exposed_name: str,
        arguments: dict[str, Any],
        *,
        max_output: int,
    ) -> tuple[str, int, str | None]:
        ref = self._tool_map.get(exposed_name)
        if not ref:
            parsed = parse_exposed_tool_name(exposed_name)
            if not parsed:
                return f"Unknown MCP tool: {exposed_name}", -1, "not found"
            server_name, tool_name = parsed
            ref = next(
                (
                    r
                    for r in self._tool_map.values()
                    if r.server == server_name and r.tool == tool_name
                ),
                None,
            )
        if not ref:
            return f"Unknown MCP tool: {exposed_name}", -1, "not found"

        server = self._servers.get(ref.server)
        if not server:
            return f"MCP server not connected: {ref.server}", -1, "not connected"

        start = time.monotonic()
        timeout = self.config.settings.tool_call_timeout_sec
        try:
            result = await asyncio.wait_for(
                server.session.call_tool(ref.tool, arguments),
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            parts: list[str] = []
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
                else:
                    parts.append(str(block))
            output = truncate_mcp_output("\n".join(parts), max_output)
            if result.isError:
                return output, 1, output
            return output, 0, None
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            msg = f"MCP tool call timed out after {timeout}s"
            return msg, -1, msg
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            msg = f"MCP tool error: {exc}"
            return msg, -1, msg

    def disconnect_all(self) -> None:
        self._loop.run_until_complete(self._disconnect_all_async())
        try:
            self._loop.close()
        except Exception:
            pass

    async def _disconnect_all_async(self) -> None:
        for server in list(self._servers.values()):
            try:
                if server.session:
                    await server.session.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                if server.transport_cm:
                    await server.transport_cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._servers.clear()
        self._tool_map.clear()

    def is_mcp_tool(self, name: str) -> bool:
        return name in self._tool_map or name.startswith("mcp__")

    def requires_approval(self, name: str) -> bool:
        ref = self._tool_map.get(name)
        return ref.require_approval if ref else True
