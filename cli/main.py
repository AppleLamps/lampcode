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
from agent.config import Config
from agent.events import AgentEvent, build_event_emitter
from agent.git import detect_repo_root
from agent.context import build_system_prompt, load_project_rules
from agent.loop import brief_args, run_turn
from agent.mcp.manager import McpManager
from agent.models import Thread, new_id, utc_now_iso
from agent.paths import default_config_path
from agent.recording.replay import format_run_human
from agent.recording.store import RunStore
from agent.settings import load_mcp_config, load_skills_config
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.exec_policy import evaluate_command, should_prompt_for_command
from agent.store import ThreadStore
from agent.tui.runner import check_tui_available, launch_tui

app = typer.Typer(no_args_is_help=True, help="Codex-inspired coding agent CLI")
threads_app = typer.Typer(help="Manage conversation threads")
config_app = typer.Typer(help="Configuration commands")
mcp_app = typer.Typer(help="MCP server commands")
skills_app = typer.Typer(help="Skill discovery commands")
exec_policy_app = typer.Typer(help="Exec policy rule testing")
runs_app = typer.Typer(help="Run recording and replay")
app.add_typer(threads_app, name="threads")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skills_app, name="skills")
app.add_typer(exec_policy_app, name="exec-policy")
app.add_typer(runs_app, name="runs")

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
        elif event.type == "compaction.completed" and not self.quiet_tools:
            console.print(
                f"[dim][compaction] removed {event.data.get('removed_items', 0)} items "
                f"({event.data.get('estimated_tokens_before', '?')} → "
                f"{event.data.get('estimated_tokens_after', '?')} tokens est.)[/dim]"
            )
        elif event.type == "sandbox.blocked":
            mode = event.data.get("mode", "")
            reason = event.data.get("reason", "")
            cmd = event.data.get("command")
            console.print(f"[yellow][sandbox][/yellow] blocked ({mode}): {reason}")
            if cmd:
                console.print(f"  command: {cmd}")
        elif event.type == "isolation.applied" and not self.quiet_tools:
            console.print(
                f"[dim][isolation][/dim] pid={event.data.get('pid')} "
                f"cwd={event.data.get('cwd')} "
                f"stripped_env={event.data.get('stripped_env_count')}"
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
    sandbox: Optional[str] = typer.Option(
        None,
        "--sandbox",
        help="Sandbox mode: danger-full-access | read-only | workspace-write",
    ),
    title: Optional[str] = typer.Option(
        None, "--title", help="Thread title (new threads only)"
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
            sandbox=sandbox,
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
            title=title,
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
        console.print(f"[dim]Thread:[/dim] {thread.display_label()} ({thread.id})")
        console.print(f"[dim]Model:[/dim] {config.model}")
        console.print(f"[dim]CWD:[/dim] {thread.cwd}")
        console.print(f"[dim]Sandbox:[/dim] {config.sandbox_mode.value}")
        if thread.repo_root:
            console.print(f"[dim]Repo:[/dim] {thread.repo_root}")
        console.print()

    output = OutputHandler(jsonl_events=jsonl_events, quiet_tools=quiet_tools)
    emitter = build_event_emitter(
        output.handle,
        recording=config.recording.enabled,
        recording_keep=config.recording.keep_last_runs_per_thread,
    )
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

    if cfg.exec_policy.mode.value == "never" and not cfg.auto_approve:
        checks.append(
            (
                "exec_policy warning",
                "exec_policy=never disables prompts; use --auto-approve in CI",
            )
        )

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

    table.add_row("sandbox mode (effective)", cfg.sandbox_mode.value)
    table.add_row(
        "exec policy",
        f"{cfg.exec_policy.mode.value} "
        f"(allow={len(cfg.exec_policy.rules.allow)}, deny={len(cfg.exec_policy.rules.deny)})",
    )
    table.add_row(
        "OpenRouter retries",
        f"max={cfg.openrouter.max_retries}, base_delay={cfg.openrouter.retry_base_delay_sec}s, "
        f"timeout={cfg.openrouter.request_timeout_sec}s",
    )
    table.add_row(
        "compaction",
        f"enabled={cfg.compaction.enabled}, threshold={cfg.compaction.threshold}",
    )
    table.add_row("thread fork", "supported (forked_from metadata in JSONL)")
    table.add_row(
        "recording",
        f"enabled={cfg.recording.enabled}, dir writable="
        f"{_runs_dir_writable()}",
    )
    table.add_row(
        "isolation",
        f"config={cfg.isolation.enabled}, effective={cfg.use_isolation}",
    )
    tui_ok, tui_msg = check_tui_available()
    table.add_row("TUI (textual)", "installed" if tui_ok else tui_msg)
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
    table.add_column("Title / lineage")
    table.add_column("CWD")
    table.add_column("Updated")
    table.add_column("Last message")
    for thread in threads:
        table.add_row(
            thread.id[:8] + "...",
            thread.display_label(),
            thread.cwd,
            thread.updated_at[:19],
            thread.last_user_message_preview(),
        )
    console.print(table)


@threads_app.command("fork")
def threads_fork(
    thread_id: str = typer.Argument(..., help="Source thread ID (full or prefix)"),
    title: Optional[str] = typer.Option(None, "--title", help="Title for forked thread"),
) -> None:
    """Fork a thread (branch conversation history)."""
    store = ThreadStore()
    source = _load_thread(store, thread_id)
    forked = store.fork_thread(source, title=title)
    console.print(f"Forked thread: {forked.id}")
    if forked.forked_from:
        console.print(f"  forked_from: {forked.forked_from}")
    if forked.title:
        console.print(f"  title: {forked.title}")


@threads_app.command("rename")
def threads_rename(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
    title: str = typer.Argument(..., help="New title"),
) -> None:
    """Rename a thread."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    store.rename_thread(thread, title)
    console.print(f"Renamed {thread.id} → {title!r}")


@exec_policy_app.command("test")
def exec_policy_test(
    command: str = typer.Argument(..., help="Shell command to evaluate"),
) -> None:
    """Test exec policy rules against a command."""
    cfg = Config.resolve()
    result = evaluate_command(command, cfg.exec_policy)
    prompt = should_prompt_for_command(command, cfg.exec_policy)
    stdout_console.print(
        {
            "command": command,
            "mode": cfg.exec_policy.mode.value,
            "decision": result["decision"],
            "reason": result["reason"],
            "auto_approve": result["auto_approve"],
            "would_prompt": prompt,
        }
    )


@threads_app.command("show")
def threads_show(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
) -> None:
    """Show a human-readable transcript."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)

    console.print(f"[bold]Thread[/bold] {thread.display_label()}")
    console.print(f"ID: {thread.id}")
    if thread.title:
        console.print(f"Title: {thread.title}")
    if thread.forked_from:
        console.print(f"Forked from: {thread.forked_from}")
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
            elif item.type == "webSearch":
                console.print(
                    f"[yellow]Web[/yellow] ({item.status}): {item.query}"
                )
                for r in item.results[:3]:
                    console.print(f"  - {r.title}: {r.url}")
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


def _runs_dir_writable() -> str:
    from agent.paths import default_runs_dir

    path = default_runs_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        return "yes"
    except OSError:
        return "no"


@app.command("tui")
def tui_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id", help="Resume thread"),
    resume_last: bool = typer.Option(False, "--resume-last", help="Resume latest thread"),
) -> None:
    """Interactive terminal UI for agent sessions."""
    try:
        launch_tui(cwd=cwd, thread_id=thread_id, resume_last=resume_last)
    except SystemExit as exc:
        raise typer.Exit(exc.code) from exc


@runs_app.command("list")
def runs_list(
    thread_id: Optional[str] = typer.Option(None, "--thread-id", help="Filter by thread"),
) -> None:
    """List recorded run logs."""
    store = RunStore()
    records = store.list_runs(thread_id)
    if not records:
        console.print("No run logs found.")
        return
    table = Table(title="Recorded Runs")
    table.add_column("Thread")
    table.add_column("Turn")
    table.add_column("Updated")
    for rec in records:
        table.add_row(rec.thread_id[:8] + "...", rec.turn_id[:8] + "...", str(int(rec.updated_at)))
    console.print(table)


@runs_app.command("show")
def runs_show(
    turn_id: str = typer.Argument(..., help="Turn ID (full or prefix)"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    as_json: bool = typer.Option(False, "--json", help="Print raw events JSONL"),
    human: bool = typer.Option(True, "--human/--no-human", help="Human transcript"),
) -> None:
    """Show a recorded run log."""
    store = RunStore()
    try:
        events = store.load_events(turn_id, thread_id=thread_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if as_json:
        for event in events:
            stdout_console.print(event.to_json())
    elif human:
        stdout_console.print(format_run_human(events))
    else:
        stdout_console.print(format_run_human(events))


@runs_app.command("replay")
def runs_replay(
    turn_id: str = typer.Argument(..., help="Turn ID (full or prefix)"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    human: bool = typer.Option(True, "--human/--no-human"),
) -> None:
    """Replay transcript from recorded events (no model call)."""
    store = RunStore()
    try:
        events = store.load_events(turn_id, thread_id=thread_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if human:
        stdout_console.print(format_run_human(events))
    else:
        for event in events:
            stdout_console.print(event.to_json())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
