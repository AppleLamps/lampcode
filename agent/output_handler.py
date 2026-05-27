from __future__ import annotations

from rich.console import Console

from agent.events import AgentEvent
from agent.loop import brief_args
from agent.models import Turn

stderr_console = Console(stderr=True)
stdout_console = Console()


def format_run_summary(turn: Turn, *, budget_exceeded: bool = False, stats: dict | None = None) -> str:
    """Single-line run completion summary for agent run."""
    u = turn.usage
    model = u.model_used or "unknown"
    fb = "true" if u.fallback_used else "false"
    cost = u.estimated_cost_usd or 0.0
    inp = u.input_tokens or 0
    out = u.output_tokens or 0
    parts = [
        f"[done] model={model} fallback={fb} cost≈${cost:.3f} tokens in={inp} out={out}",
    ]
    if budget_exceeded:
        parts.append("budget_exceeded=true")
    if stats:
        from agent.turn_stats import TurnStats

        if isinstance(stats, TurnStats):
            stats = stats.to_dict()
        brief = (
            f"files={stats.get('files_touched', 0)} "
            f"+{stats.get('lines_added', 0)}/-{stats.get('lines_removed', 0)} "
            f"cmds={stats.get('commands_run', 0)}"
        )
        if stats.get("tests_detected"):
            brief += " tests=yes"
        parts.append(brief)
    return " ".join(parts)


class OutputHandler:
    def __init__(
        self,
        *,
        jsonl_events: bool = False,
        json_stream: bool = False,
        quiet_tools: bool = False,
        stderr: Console | None = None,
        stdout: Console | None = None,
    ) -> None:
        self.jsonl_events = jsonl_events
        self.json_stream = json_stream
        self.quiet_tools = quiet_tools
        self._stderr = stderr or stderr_console
        self._stdout = stdout or stdout_console
        self._stream_handler = None
        if json_stream:
            from agent.json_stream import JsonStreamHandler

            self._stream_handler = JsonStreamHandler()

    @property
    def stream_handler(self):
        return self._stream_handler

    def handle(self, event: AgentEvent) -> None:
        if self.json_stream and self._stream_handler:
            line = self._stream_handler.handle(event)
            if line:
                self._stdout.print(line)
            return
        if self.jsonl_events:
            self._stdout.print(event.to_json())
            return

        if event.type == "agent.delta":
            self._stdout.print(event.data.get("text", ""), end="")
        elif event.type == "tool.pending" and not self.quiet_tools:
            name = event.data.get("tool_name", "")
            args = event.data.get("arguments", {})
            self._stderr.print(f"[cyan][tool][/cyan] {name}: {brief_args(name, args)}")
        elif event.type == "tool.completed" and not self.quiet_tools:
            name = event.data.get("tool_name", "")
            status = event.data.get("status", "ok")
            self._stderr.print(f"[dim][tool done][/dim] {name}: {status}")
            if name == "apply_patch":
                from agent.tui.diff_render import format_diff_lines, infer_path_from_diff

                diff = event.data.get("diff_preview") or event.data.get("summary")
                if diff:
                    path = infer_path_from_diff(str(diff))
                    for ln in format_diff_lines(
                        str(diff),
                        max_lines=12,
                        line_numbers=True,
                        source_path=path,
                        hunk_aware=True,
                    ):
                        self._stderr.print(f"[dim]  {ln}[/dim]")
        elif event.type == "approval.requested":
            summary = event.data.get("summary", "")
            self._stderr.print(f"[yellow][approval pending][/yellow] {summary}")
        elif event.type == "compaction":
            self._stderr.print(
                f"[dim][compaction] summarized {event.data.get('summarized_items', 0)} items[/dim]"
            )
        elif event.type == "compaction.completed" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][compaction] removed {event.data.get('removed_items', 0)} items "
                f"({event.data.get('estimated_tokens_before', '?')} → "
                f"{event.data.get('estimated_tokens_after', '?')} tokens est.)[/dim]"
            )
        elif event.type == "sandbox.blocked":
            mode = event.data.get("mode", "")
            reason = event.data.get("reason", "")
            cmd = event.data.get("command")
            self._stderr.print(f"[yellow][sandbox][/yellow] blocked ({mode}): {reason}")
            if cmd:
                self._stderr.print(f"  command: {cmd}")
        elif event.type == "isolation.applied" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][isolation][/dim] pid={event.data.get('pid')} "
                f"cwd={event.data.get('cwd')} "
                f"stripped_env={event.data.get('stripped_env_count')}"
            )
        elif event.type == "execution.backend.selected" and not self.quiet_tools:
            backend = event.data.get("backend", "")
            image = event.data.get("image")
            extra = f" ({image})" if image else ""
            self._stderr.print(f"[dim][execution][/dim] backend={backend}{extra}")
        elif event.type == "execution.docker.started" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][docker][/dim] started image={event.data.get('image')} "
                f"container={event.data.get('container_id', '')[:12]}"
            )
        elif event.type == "execution.docker.completed" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][docker][/dim] exit={event.data.get('exit_code')} "
                f"duration={event.data.get('duration_ms')}ms"
            )
        elif event.type == "collab.spawn.started" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][worker][/dim] spawning: {event.data.get('task', '')[:80]}"
            )
        elif event.type == "collab.spawn.completed" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][worker][/dim] {event.data.get('status')}: "
                f"{event.data.get('worker_id') or event.data.get('worker_thread_id', '')[:8]}"
            )
        elif event.type == "execution.ssh.connected" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][ssh][/dim] connected {event.data.get('user')}@{event.data.get('host')}"
            )
        elif event.type == "execution.ssh.completed" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][ssh][/dim] exit={event.data.get('exit_code')} "
                f"duration={event.data.get('duration_ms')}ms"
            )
        elif event.type == "execution.docker.file_tool_applied" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][docker][/dim] file tool {event.data.get('tool_name')} "
                f"on {event.data.get('path')}"
            )
        elif event.type == "execution.sync.started" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][sync][/dim] {event.data.get('direction')} via "
                f"{event.data.get('transport')} (~{event.data.get('bytes_estimated')} bytes)"
            )
        elif event.type == "execution.sync.completed" and not self.quiet_tools:
            self._stderr.print(
                f"[dim][sync][/dim] {event.data.get('direction')} done "
                f"{event.data.get('files')} files in {event.data.get('duration_ms')}ms"
            )
        elif event.type == "execution.sync.failed" and not self.quiet_tools:
            self._stderr.print(f"[yellow][sync failed][/yellow] {event.data.get('reason')}")
        elif event.type == "execution.sync.plan" and not self.quiet_tools:
            counts = event.data.get("counts", {})
            self._stderr.print(
                f"[dim][sync plan][/dim] push={counts.get('push', 0)} "
                f"pull={counts.get('pull', 0)} conflicts={counts.get('conflict', 0)}"
            )
        elif event.type == "execution.ssh.pool.acquire" and not self.quiet_tools:
            self._stderr.print(f"[dim][ssh pool][/dim] acquire {event.data.get('host')}")
        elif event.type == "collab.checkpoint.saved" and not self.quiet_tools:
            self._stderr.print(f"[dim][checkpoint][/dim] saved {event.data.get('path')}")
        elif event.type == "error":
            self._stderr.print(f"[red]Error:[/red] {event.data.get('message', '')}")
        elif event.type == "turn.completed" and event.data.get("status") == "cancelled":
            self._stderr.print("[yellow]Turn cancelled.[/yellow]")
        elif event.type == "mcp.server.failed" and not self.quiet_tools:
            self._stderr.print(
                f"[yellow]MCP server failed:[/yellow] {event.data.get('server')} — {event.data.get('error')}"
            )
