from __future__ import annotations

import shutil
import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agent.cancel import CancelToken, CancelledError
from agent.config import Config, default_config_path
from agent.events import AgentEvent, EventEmitter
from agent.git import detect_repo_root
from agent.context import build_system_prompt, load_project_rules
from agent.loop import brief_args, run_turn
from agent.mcp.manager import McpManager
from agent.models import Thread, new_id, utc_now_iso
from agent.settings import load_mcp_config, load_skills_config
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.store import ThreadStore

app = typer.Typer(no_args_is_help=True, help="Codex-inspired coding agent CLI")
threads_app = typer.Typer(help="Manage conversation threads")
config_app = typer.Typer(help="Configuration commands")
mcp_app = typer.Typer(help="MCP server commands")
skills_app = typer.Typer(help="Skill discovery commands")
app.add_typer(threads_app, name="threads")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skills_app, name="skills")

console = Console(stderr=True)
stdout_console = Console()


class OutputHandler:
    def __init__(
        self,
        *,
        jsonl_events: bool = False,
        quiet_tools: bool = False,
    ) -> None:
        self.jsonl_events = jsonl_events
        self.quiet_tools = quiet_tools

    def handle(self, event: AgentEvent) -> None:
        if self.jsonl_events:
            stdout_console.print(event.to_json())
            return

        if event.type == "agent.delta":
            stdout_console.print(event.data.get("text", ""), end="")
        elif event.type == "tool.pending" and not self.quiet_tools:
            name = event.data.get("tool_name", "")
            args = event.data.get("arguments", {})
            console.print(f"[cyan][tool][/cyan] {name}: {brief_args(name, args)}")
        elif event.type == "approval.requested":
            pass  # interactive prompt handled in approval.gate
        elif event.type == "compaction":
            console.print(
                f"[dim][compaction] summarized {event.data.get('summarized_items', 0)} items[/dim]"
            )
        elif event.type == "error":
            console.print(f"[red]Error:[/red] {event.data.get('message', '')}")
        elif event.type == "turn.completed" and event.data.get("status") == "cancelled":
            console.print("[yellow]Turn cancelled.[/yellow]")
        elif event.type == "mcp.server.failed" and not self.quiet_tools:
            console.print(
                f"[yellow]MCP server failed:[/yellow] {event.data.get('server')} — {event.data.get('error')}"
            )


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Task for the agent"),
    cwd: Optional[Path] = typer.Option(
        None, "--cwd", help="Project directory (defaults to current directory)"
    ),
    thread_id: Optional[str] = typer.Option(
        None, "--thread-id", help="Resume an existing thread"
    ),
    resume_last: bool = typer.Option(
        False, "--resume-last", help="Resume most recent thread for same cwd"
    ),
    model: Optional[str] = typer.Option(None, "--model", help="OpenRouter model slug"),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Skip approval prompts for commands and writes"
    ),
    max_rounds: Optional[int] = typer.Option(
        None, "--max-rounds", help="Max tool-call rounds"
    ),
    skip_git_check: bool = typer.Option(
        False, "--skip-git-check", help="Do not warn when cwd is outside a git repo"
    ),
    jsonl_events: bool = typer.Option(
        False, "--jsonl-events", help="Emit machine-readable JSONL events on stdout"
    ),
    quiet_tools: bool = typer.Option(
        False, "--quiet-tools", help="Hide tool action lines; show approvals + answer"
    ),
    session_auto_approve: bool = typer.Option(
        False,
        "--session-auto-approve",
        help="Approve all shell/write/MCP actions for this thread session",
    ),
    show_system_prompt: bool = typer.Option(
        False,
        "--show-system-prompt",
        help="Print resolved system prompt before running (debug)",
    ),
) -> None:
    """Run the agent on a task."""
    try:
        config = Config.resolve(
            cwd=cwd,
            model=model,
            auto_approve=auto_approve if auto_approve else None,
            max_rounds=max_rounds,
            skip_git_check=skip_git_check,
        )
        config.require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    repo_root = detect_repo_root(config.cwd)
    if not skip_git_check and repo_root is None:
        console.print(
            "[yellow]Warning:[/yellow] cwd is not inside a git repository. "
            "Use --skip-git-check to suppress."
        )

    store = ThreadStore()
    thread: Thread | None = None

    if thread_id:
        thread = _load_thread(store, thread_id)
    elif resume_last:
        thread = store.find_latest_for_cwd(str(config.cwd))
        if not thread:
            console.print(
                f"[red]Error:[/red] No previous thread found for cwd: {config.cwd}"
            )
            raise typer.Exit(1)

    if thread:
        if model:
            thread.model = model
            config.model = model
        if repo_root and not thread.repo_root:
            thread.repo_root = repo_root
            store.save_thread(thread)
    else:
        thread = Thread(
            id=new_id(),
            cwd=str(config.cwd),
            model=config.model,
            repo_root=repo_root,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        store.create_thread(thread)

    skills_cfg = load_skills_config(config.config_path)
    active_skills = select_skills(
        discover_skills(
            config.cwd,
            enable_project=skills_cfg.enable_project_skills,
            enable_user=skills_cfg.enable_user_skills,
        ),
        prompt,
        max_active=skills_cfg.max_active,
    )
    rules_text, _ = load_project_rules(
        config.cwd, max_chars=skills_cfg.project_rules_max_chars
    )

    if show_system_prompt:
        stdout_console.print(
            build_system_prompt(
                config.cwd,
                thread.repo_root,
                active_skills=active_skills,
                skills_max_body=skills_cfg.max_body_chars,
                project_rules=rules_text,
            )
        )
        stdout_console.print("---")

    if session_auto_approve and not jsonl_events:
        console.print("[dim][approval] session auto-approve enabled[/dim]")

    if not jsonl_events:
        console.print(f"[dim]Thread:[/dim] {thread.id}")
        console.print(f"[dim]Model:[/dim] {config.model}")
        console.print(f"[dim]CWD:[/dim] {thread.cwd}")
        if thread.repo_root:
            console.print(f"[dim]Repo:[/dim] {thread.repo_root}")
        console.print()

    output = OutputHandler(jsonl_events=jsonl_events, quiet_tools=quiet_tools)
    emitter = EventEmitter(output.handle)
    cancel_token = CancelToken()

    def _handle_sigint(signum, frame) -> None:  # noqa: ARG001
        cancel_token.cancel()

    previous = signal.signal(signal.SIGINT, _handle_sigint)

    try:
        turn = run_turn(
            thread,
            prompt,
            config,
            store,
            events=emitter,
            cancel_token=cancel_token,
            quiet_tools=quiet_tools,
            session_auto_approve=session_auto_approve,
        )
    except CancelledError:
        if not jsonl_events:
            console.print()
            console.print(f"[yellow]Cancelled.[/yellow] Thread: {thread.id}")
        raise typer.Exit(130) from None
    except Exception as exc:
        console.print()
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        signal.signal(signal.SIGINT, previous)

    if not jsonl_events:
        console.print()
        if turn.status == "completed":
            console.print(f"[green]Done.[/green] Thread: {thread.id}")
        elif turn.status == "cancelled":
            console.print(f"[yellow]Cancelled.[/yellow] Thread: {thread.id}")
        else:
            console.print(f"[yellow]Turn {turn.status}.[/yellow] Thread: {thread.id}")

    if turn.status == "cancelled":
        raise typer.Exit(130)


@config_app.command("show")
def config_show(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory"),
    model: Optional[str] = typer.Option(None, "--model"),
    auto_approve: bool = typer.Option(False, "--auto-approve"),
) -> None:
    """Print effective resolved configuration."""
    config = Config.resolve(
        cwd=cwd,
        model=model,
        auto_approve=auto_approve if auto_approve else None,
    )
    stdout_console.print(config.to_json())


@app.command()
def doctor(
    deep: bool = typer.Option(False, "--deep", help="Try starting configured MCP servers"),
) -> None:
    """Check environment: API key, git, ripgrep, node/npx, skills, config."""
    config_path = default_config_path()
    cfg = Config.resolve()
    api_key = cfg.openrouter_api_key
    git_ok = shutil.which("git") is not None
    rg_ok = shutil.which("rg") is not None
    node_ok = shutil.which("node") is not None
    npx_ok = shutil.which("npx") is not None
    mcp_cfg = load_mcp_config(config_path, cfg.cwd)
    enabled_mcp = [n for n, s in mcp_cfg.servers.items() if s.enabled]
    skills_cfg = load_skills_config(config_path)
    skill_count = len(
        discover_skills(
            cfg.cwd,
            enable_project=skills_cfg.enable_project_skills,
            enable_user=skills_cfg.enable_user_skills,
        )
    )

    checks = [
        ("OPENROUTER_API_KEY", "set" if api_key else "MISSING"),
        ("git", "found" if git_ok else "not found"),
        ("ripgrep (rg)", "found" if rg_ok else "not found (Python fallback)"),
        ("node", "found" if node_ok else "not found"),
        ("npx", "found" if npx_ok else "not found"),
        ("config file", str(config_path) if config_path.exists() else "not found"),
        ("MCP servers enabled", str(len(enabled_mcp))),
        ("skills discovered", str(skill_count)),
    ]

    if enabled_mcp and not npx_ok:
        checks.append(("MCP warning", "npx missing but MCP servers configured"))

    table = Table(title="agent doctor")
    table.add_column("Check")
    table.add_column("Status")
    for name, status in checks:
        style = "green" if status not in ("MISSING", "not found") else "yellow"
        if name == "OPENROUTER_API_KEY" and status == "MISSING":
            style = "red"
        if "warning" in name.lower():
            style = "yellow"
        table.add_row(name, f"[{style}]{status}[/{style}]")
    console.print(table)

    if deep and enabled_mcp:
        console.print("[dim]Deep check: connecting MCP servers...[/dim]")
        manager = McpManager(mcp_cfg)
        failed: list[str] = []
        manager.connect_all(
            on_failed=lambda s, e: failed.append(f"{s}: {e}"),
        )
        console.print(f"MCP tools discovered: {len(manager.tool_map)}")
        if failed:
            console.print(f"[yellow]Failures:[/yellow] {', '.join(failed)}")
        manager.disconnect_all()


@mcp_app.command("list")
def mcp_list(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
) -> None:
    """Show configured MCP servers."""
    cfg = Config.resolve(cwd=cwd)
    mcp_cfg = load_mcp_config(cfg.config_path, cfg.cwd)
    if not mcp_cfg.servers:
        console.print("No MCP servers configured.")
        return
    table = Table(title="MCP Servers")
    table.add_column("Name")
    table.add_column("Enabled")
    table.add_column("Command")
    table.add_column("Require approval")
    for name, srv in mcp_cfg.servers.items():
        table.add_row(
            name,
            "yes" if srv.enabled else "no",
            f"{srv.command} {' '.join(srv.args)}".strip(),
            "yes" if srv.require_approval else "no",
        )
    console.print(table)


@mcp_app.command("tools")
def mcp_tools(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
) -> None:
    """Connect to MCP servers and list discovered tools."""
    cfg = Config.resolve(cwd=cwd)
    mcp_cfg = load_mcp_config(cfg.config_path, cfg.cwd)
    manager = McpManager(mcp_cfg)
    try:
        manager.connect_all(
            on_failed=lambda s, e: console.print(f"[yellow]{s} failed:[/yellow] {e}"),
        )
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not manager.tool_map:
        console.print("No MCP tools discovered.")
    else:
        table = Table(title="MCP Tools")
        table.add_column("Exposed name")
        table.add_column("Server")
        table.add_column("Tool")
        for ref in manager.tool_map.values():
            table.add_row(ref.exposed_name, ref.server, ref.tool)
        console.print(table)
    manager.disconnect_all()


@skills_app.command("list")
def skills_list(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
) -> None:
    """Discover and list available skills."""
    cfg = Config.resolve(cwd=cwd)
    skills_cfg = load_skills_config(cfg.config_path)
    skills = discover_skills(
        cfg.cwd,
        enable_project=skills_cfg.enable_project_skills,
        enable_user=skills_cfg.enable_user_skills,
    )
    if not skills:
        console.print("No skills found.")
        return
    table = Table(title="Skills")
    table.add_column("Name")
    table.add_column("Source")
    table.add_column("Description")
    for skill in skills:
        table.add_row(skill.name, skill.source, skill.description[:80])
    console.print(table)


@skills_app.command("show")
def skills_show(
    name: str = typer.Argument(..., help="Skill name"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
) -> None:
    """Show a skill's SKILL.md body."""
    cfg = Config.resolve(cwd=cwd)
    skills_cfg = load_skills_config(cfg.config_path)
    skills = discover_skills(
        cfg.cwd,
        enable_project=skills_cfg.enable_project_skills,
        enable_user=skills_cfg.enable_user_skills,
    )
    match = next((s for s in skills if s.name == name), None)
    if not match:
        console.print(f"[red]Skill not found:[/red] {name}")
        raise typer.Exit(1)
    stdout_console.print(f"# {match.name}\n")
    stdout_console.print(f"*{match.description}*\n")
    stdout_console.print(match.body)


@threads_app.command("list")
def threads_list() -> None:
    """List saved conversation threads."""
    store = ThreadStore()
    threads = store.list_threads()
    if not threads:
        console.print("No threads found.")
        return

    table = Table(title="Agent Threads")
    table.add_column("ID", style="cyan")
    table.add_column("CWD")
    table.add_column("Updated")
    table.add_column("Last message")
    for thread in threads:
        table.add_row(
            thread.id[:8] + "...",
            thread.cwd,
            thread.updated_at[:19],
            thread.last_user_message_preview(),
        )
    console.print(table)


@threads_app.command("show")
def threads_show(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
) -> None:
    """Show a human-readable transcript."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)

    console.print(f"[bold]Thread[/bold] {thread.id}")
    console.print(f"CWD: {thread.cwd}")
    if thread.repo_root:
        console.print(f"Repo root: {thread.repo_root}")
    console.print(f"Model: {thread.model}")
    console.print(f"Created: {thread.created_at}")
    console.print(f"Updated: {thread.updated_at}")
    console.print()

    for i, turn in enumerate(thread.turns, start=1):
        console.print(f"[bold]--- Turn {i} ({turn.status}) ---[/bold]")
        for item in turn.items:
            if item.type == "userMessage":
                console.print(f"[blue]User:[/blue] {item.text}")
            elif item.type == "agentMessage":
                console.print(f"[green]Agent:[/green] {item.text}")
            elif item.type == "commandExecution":
                console.print(
                    f"[yellow]Command[/yellow] ({item.status}): {item.command}"
                )
                if item.output:
                    console.print(item.output[:500])
            elif item.type == "fileChange":
                change = item.change_type or "change"
                console.print(
                    f"[yellow]File {change}[/yellow] ({item.status}): {item.path}"
                )
                if item.summary:
                    console.print(item.summary)
                if item.diff_snippet:
                    console.print(item.diff_snippet[:300])
            elif item.type == "contextCompaction":
                console.print("[dim]Context compaction checkpoint[/dim]")
            elif item.type == "skillActivation":
                console.print(f"[dim]Skills activated:[/dim] {', '.join(item.skills)}")
            elif item.type == "mcpToolCall":
                console.print(
                    f"[yellow]MCP[/yellow] ({item.status}) {item.server}.{item.tool}"
                )
                if item.output:
                    console.print(item.output[:500])
        console.print()


@threads_app.command("delete")
def threads_delete(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a saved thread."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    if not yes:
        confirm = typer.confirm(f"Delete thread {thread.id}?")
        if not confirm:
            raise typer.Exit(0)
    store.delete_thread(thread.id)
    console.print(f"Deleted thread {thread.id}")


def _load_thread(store: ThreadStore, thread_id: str) -> Thread:
    try:
        return store.load_thread(thread_id)
    except FileNotFoundError:
        pass

    matches = [
        t
        for t in store.list_threads()
        if t.id.startswith(thread_id) or t.id == thread_id
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        console.print(f"[red]Error:[/red] Ambiguous thread prefix: {thread_id}")
        raise typer.Exit(1)

    console.print(f"[red]Error:[/red] Thread not found: {thread_id}")
    raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
