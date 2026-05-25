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
from agent.export.html import export_thread_html
from agent.export.markdown import export_run_markdown, export_thread_markdown
from agent.execution.docker import check_docker_available, check_docker_hello_world
from agent.execution.factory import backend_display, run_execution_test
from agent.execution.ssh import check_ssh_available, validate_ssh_config
from agent.execution.sync.service import (
    resolve_sync_path,
    run_sync_plan,
    run_sync_pull,
    run_sync_push,
    sync_status,
)
from agent.execution.sync.planner import detect_sync_tools, estimate_sync_size
from agent.multi_agent.checkpoint import CheckpointStore
from agent.multi_agent.resume import list_checkpoint_status, resume_supervisor_turn
from agent.recording.replay import format_run_human
from agent.recording.store import RunStore
from agent.metrics import MetricsCollector
from agent.settings import load_serve_settings
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
execution_app = typer.Typer(help="Execution backend commands")
sync_app = typer.Typer(help="SSH workspace sync commands")
multi_agent_app = typer.Typer(help="Multi-agent supervisor commands")
metrics_app = typer.Typer(help="Runtime metrics")
app.add_typer(threads_app, name="threads")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skills_app, name="skills")
app.add_typer(exec_policy_app, name="exec-policy")
app.add_typer(runs_app, name="runs")
app.add_typer(execution_app, name="execution")
app.add_typer(sync_app, name="sync")
app.add_typer(multi_agent_app, name="multi-agent")
app.add_typer(metrics_app, name="metrics")

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
        elif event.type == "execution.backend.selected" and not self.quiet_tools:
            backend = event.data.get("backend", "")
            image = event.data.get("image")
            extra = f" ({image})" if image else ""
            console.print(f"[dim][execution][/dim] backend={backend}{extra}")
        elif event.type == "execution.docker.started" and not self.quiet_tools:
            console.print(
                f"[dim][docker][/dim] started image={event.data.get('image')} "
                f"container={event.data.get('container_id', '')[:12]}"
            )
        elif event.type == "execution.docker.completed" and not self.quiet_tools:
            console.print(
                f"[dim][docker][/dim] exit={event.data.get('exit_code')} "
                f"duration={event.data.get('duration_ms')}ms"
            )
        elif event.type == "collab.spawn.started" and not self.quiet_tools:
            console.print(
                f"[dim][worker][/dim] spawning: {event.data.get('task', '')[:80]}"
            )
        elif event.type == "collab.spawn.completed" and not self.quiet_tools:
            console.print(
                f"[dim][worker][/dim] {event.data.get('status')}: "
                f"{event.data.get('worker_id') or event.data.get('worker_thread_id', '')[:8]}"
            )
        elif event.type == "execution.ssh.connected" and not self.quiet_tools:
            console.print(
                f"[dim][ssh][/dim] connected {event.data.get('user')}@{event.data.get('host')}"
            )
        elif event.type == "execution.ssh.completed" and not self.quiet_tools:
            console.print(
                f"[dim][ssh][/dim] exit={event.data.get('exit_code')} "
                f"duration={event.data.get('duration_ms')}ms"
            )
        elif event.type == "execution.docker.file_tool_applied" and not self.quiet_tools:
            console.print(
                f"[dim][docker][/dim] file tool {event.data.get('tool_name')} "
                f"on {event.data.get('path')}"
            )
        elif event.type == "execution.sync.started" and not self.quiet_tools:
            console.print(
                f"[dim][sync][/dim] {event.data.get('direction')} via "
                f"{event.data.get('transport')} (~{event.data.get('bytes_estimated')} bytes)"
            )
        elif event.type == "execution.sync.completed" and not self.quiet_tools:
            console.print(
                f"[dim][sync][/dim] {event.data.get('direction')} done "
                f"{event.data.get('files')} files in {event.data.get('duration_ms')}ms"
            )
        elif event.type == "execution.sync.failed" and not self.quiet_tools:
            console.print(f"[yellow][sync failed][/yellow] {event.data.get('reason')}")
        elif event.type == "execution.sync.plan" and not self.quiet_tools:
            counts = event.data.get("counts", {})
            console.print(
                f"[dim][sync plan][/dim] push={counts.get('push', 0)} "
                f"pull={counts.get('pull', 0)} conflicts={counts.get('conflict', 0)}"
            )
        elif event.type == "execution.ssh.pool.acquire" and not self.quiet_tools:
            console.print(f"[dim][ssh pool][/dim] acquire {event.data.get('host')}")
        elif event.type == "collab.checkpoint.saved" and not self.quiet_tools:
            console.print(f"[dim][checkpoint][/dim] saved {event.data.get('path')}")
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
    execution_backend: Optional[str] = typer.Option(
        None,
        "--execution-backend",
        help="Command execution backend: local | docker | ssh",
    ),
    docker_image: Optional[str] = typer.Option(
        None, "--docker-image", help="Docker image override for run_command"
    ),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host", help="SSH host override"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user", help="SSH user override"),
    ssh_identity_file: Optional[str] = typer.Option(
        None, "--ssh-identity-file", help="SSH identity file path"
    ),
    multi_agent: bool = typer.Option(
        False, "--multi-agent", help="Enable spawn_worker supervisor tool"
    ),
    sync_mode: Optional[str] = typer.Option(
        None, "--sync", help="SSH sync mode: push | push-pull | manual"
    ),
    force_sync: bool = typer.Option(
        False, "--force-sync", help="Bypass sync upload size limit"
    ),
    resume_multi_agent: bool = typer.Option(
        False, "--resume-multi-agent", help="Resume supervisor turn from checkpoint"
    ),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Retry failed workers when resuming checkpoint"
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
            execution_backend=execution_backend,
            docker_image=docker_image,
            ssh_host=ssh_host,
            ssh_user=ssh_user,
            ssh_identity_file=ssh_identity_file,
            multi_agent=multi_agent if multi_agent else None,
            sync_mode=sync_mode,
            force_sync=force_sync,
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
        console.print(f"[dim]Execution:[/dim] {backend_display(config)}")
        if config.multi_agent.enabled:
            console.print("[dim]Multi-agent:[/dim] enabled (spawn_worker)")
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
        if resume_multi_agent and thread:
            cp_store = CheckpointStore(
                Path(config.multi_agent.checkpoint_dir).expanduser()
            )
            cp = cp_store.find_latest(thread.id)
            if not cp:
                console.print("[red]Error:[/red] No checkpoint found for resume.")
                raise typer.Exit(1)
            turn = resume_supervisor_turn(
                thread,
                cp,
                config,
                store,
                retry_failed=retry_failed,
                events=emitter,
            )
        else:
            turn = run_turn(
                thread,
                prompt,
                config,
                store,
                events=emitter,
                cancel_token=cancel_token,
                quiet_tools=quiet_tools,
                session_auto_approve=session_auto_approve,
                force_sync=force_sync,
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
    table.add_row("execution backend", cfg.execution.backend)
    table.add_row(
        "multi-agent",
        f"enabled={cfg.multi_agent.enabled}, max_workers={cfg.multi_agent.max_workers_per_turn}, "
        f"max_depth={cfg.multi_agent.max_worker_depth}, max_concurrent={cfg.multi_agent.max_concurrent_workers}",
    )
    table.add_row(
        "docker file tools",
        "enabled" if cfg.execution.docker.file_tools_in_container else "disabled",
    )
    docker_bin = cfg.execution.docker.binary
    docker_ok, docker_msg = check_docker_available(docker_bin)
    table.add_row(f"docker ({docker_bin})", docker_msg if docker_ok else f"unavailable: {docker_msg}")
    ssh_ok, ssh_msg = check_ssh_available()
    table.add_row("ssh", ssh_msg if ssh_ok else f"unavailable: {ssh_msg}")
    if cfg.execution.backend == "ssh" or cfg.execution.ssh.host:
        ssh_cfg_ok, ssh_cfg_msg = validate_ssh_config(cfg.execution.ssh)
        table.add_row("ssh config", ssh_cfg_msg if ssh_cfg_ok else f"incomplete: {ssh_cfg_msg}")
        tools = detect_sync_tools()
        table.add_row("sync rsync", tools.get("rsync", "n/a"))
        table.add_row("sync scp", tools.get("scp", "n/a"))
        table.add_row(
            "ssh sync",
            f"enabled={cfg.execution.ssh.sync_enabled}, mode={cfg.execution.ssh.sync_mode}, "
            f"incremental={cfg.execution.ssh.sync.mode}",
        )
        sync_state_dir = Path.home() / ".agent-cli" / "sync-state"
        try:
            sync_state_dir.mkdir(parents=True, exist_ok=True)
            sync_state_writable = "yes"
        except OSError:
            sync_state_writable = "no"
        table.add_row("sync-state dir", f"writable={sync_state_writable}")
        serve_cfg = load_serve_settings(cfg.config_path)
        table.add_row(
            "serve",
            f"control={serve_cfg.enable_control}, token={'set' if serve_cfg.auth_token else 'auto'}",
        )
        table.add_row(
            "metrics",
            f"enabled={cfg.multi_agent.metrics_enabled}",
        )
        if cfg.cwd.is_dir():
            b, n = estimate_sync_size(
                cfg.cwd,
                excludes=list(cfg.execution.ssh.sync.exclude),
                include_dotfiles=cfg.execution.ssh.sync.include_dotfiles,
            )
            table.add_row("sync cwd estimate", f"{n} files, {b / (1024*1024):.1f} MB")
        table.add_row(
            "ssh pool",
            f"enabled={cfg.execution.ssh.pool.enabled}, max={cfg.execution.ssh.pool.max_sessions}",
        )
    cp_dir = Path(cfg.multi_agent.checkpoint_dir).expanduser()
    try:
        cp_dir.mkdir(parents=True, exist_ok=True)
        cp_writable = "yes"
    except OSError:
        cp_writable = "no"
    table.add_row(
        "checkpoints",
        f"enabled={cfg.multi_agent.checkpoint_enabled}, dir writable={cp_writable}",
    )
    console.print(table)

    if deep:
        import os

        skip_docker = os.environ.get("AGENT_SKIP_DOCKER_INTEGRATION") == "1"
        if docker_ok and not skip_docker:
            hw_ok, hw_msg = check_docker_hello_world(docker_bin, skip=skip_docker)
            console.print(
                f"[green]Docker hello-world:[/green] {hw_msg}"
                if hw_ok
                else f"[yellow]Docker hello-world failed:[/yellow] {hw_msg}"
            )
        elif skip_docker:
            console.print("[dim]Docker integration check skipped (AGENT_SKIP_DOCKER_INTEGRATION=1)[/dim]")

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


@threads_app.command("export")
def threads_export(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output file"),
    format: str = typer.Option("markdown", "--format", help="Export format: markdown | html"),
) -> None:
    """Export thread transcript to Markdown or HTML."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    cfg = Config.resolve(cwd=Path(thread.cwd))
    if format == "html":
        content = export_thread_html(
            thread,
            sandbox=cfg.sandbox_mode.value,
            backend=cfg.execution.backend,
        )
        default_ext = ".html"
    else:
        content = export_thread_markdown(
            thread,
            sandbox=cfg.sandbox_mode.value,
            backend=cfg.execution.backend,
        )
        default_ext = ".md"
    if out:
        out.write_text(content, encoding="utf-8")
        console.print(f"Exported to {out}")
    else:
        stdout_console.print(content)


@execution_app.command("test")
def execution_test(
    cmd: str = typer.Option("echo ok", "--cmd", help="Command to run"),
    backend: str = typer.Option("local", "--backend", help="local | docker | ssh"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory"),
    docker_image: Optional[str] = typer.Option(None, "--docker-image"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
) -> None:
    """Test an execution backend without running the full agent."""
    try:
        config = Config.resolve(
            cwd=cwd,
            execution_backend=backend,
            docker_image=docker_image,
            ssh_host=ssh_host,
            ssh_user=ssh_user,
            auto_approve=True,
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if backend == "ssh":
        ok, msg = validate_ssh_config(config.execution.ssh)
        if not ok:
            console.print(f"[red]SSH config invalid:[/red] {msg}")
            raise typer.Exit(1)

    result = run_execution_test(config, cmd)
    console.print(f"[dim]Backend:[/dim] {result['backend']}")
    console.print(f"[dim]Exit code:[/dim] {result['exit_code']}")
    console.print(f"[dim]Duration:[/dim] {result['duration_ms']}ms")
    stdout_console.print(result["output"])


def _resolve_ssh_config(
    cwd: Optional[Path],
    ssh_host: Optional[str],
    ssh_user: Optional[str],
) -> Config:
    return Config.resolve(cwd=cwd, ssh_host=ssh_host, ssh_user=ssh_user, auto_approve=True)


@sync_app.command("plan")
def sync_plan_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
) -> None:
    """Dry-run sync plan: push/pull/conflict classification."""
    import json

    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.backend = "ssh"
    config.execution.ssh.sync_enabled = True
    stdout_console.print(json.dumps(run_sync_plan(config, thread_id=thread_id), indent=2))


@sync_app.command("resolve")
def sync_resolve_cmd(
    path: str = typer.Option(..., "--path"),
    strategy: str = typer.Option("local-wins", "--strategy"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
) -> None:
    """Resolve a sync conflict for a single path."""
    import json

    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.backend = "ssh"
    config.execution.ssh.sync_enabled = True
    stdout_console.print(json.dumps(resolve_sync_path(config, path, strategy, thread_id=thread_id), indent=2))


@sync_app.command("push")
def sync_push_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
    force: bool = typer.Option(False, "--force-sync"),
    incremental: bool = typer.Option(True, "--incremental/--full"),
) -> None:
    """Push local workspace to remote SSH host."""
    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.backend = "ssh"
    config.execution.ssh.sync_enabled = True
    result, item = run_sync_push(config, force=force, incremental=incremental)
    if not result.ok:
        console.print(f"[red]Sync push failed:[/red] {result.error or result.summary}")
        raise typer.Exit(1)
    console.print(f"[green]Sync push OK[/green] via {result.transport} ({result.duration_ms}ms)")


@sync_app.command("pull")
def sync_pull_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
    incremental: bool = typer.Option(True, "--incremental/--full"),
) -> None:
    """Pull remote workspace to local cwd."""
    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.backend = "ssh"
    config.execution.ssh.sync_enabled = True
    result, item = run_sync_pull(config, incremental=incremental)
    if not result.ok:
        console.print(f"[red]Sync pull failed:[/red] {result.error or result.summary}")
        raise typer.Exit(1)
    console.print(f"[green]Sync pull OK[/green] via {result.transport} ({result.duration_ms}ms)")


@sync_app.command("status")
def sync_status_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
) -> None:
    """Show SSH sync configuration and cwd estimate."""
    import json

    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.ssh.sync_enabled = True
    stdout_console.print(json.dumps(sync_status(config), indent=2))


@multi_agent_app.command("status")
def multi_agent_status(
    thread_id: str = typer.Option(..., "--thread-id"),
) -> None:
    """Show multi-agent checkpoint status for a thread."""
    import json

    rows = list_checkpoint_status(thread_id)
    stdout_console.print(json.dumps(rows, indent=2))


@multi_agent_app.command("resume")
def multi_agent_resume_cmd(
    thread_id: str = typer.Option(..., "--thread-id"),
    turn_id: Optional[str] = typer.Option(None, "--turn-id"),
    retry_failed: bool = typer.Option(False, "--retry-failed"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Resume a supervisor turn from checkpoint."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    config = Config.resolve(cwd=cwd or Path(thread.cwd), multi_agent=True)
    config.require_api_key()
    cp_store = CheckpointStore(Path(config.multi_agent.checkpoint_dir).expanduser())
    cp = cp_store.load(thread.id, turn_id) if turn_id else cp_store.find_latest(thread.id)
    if not cp:
        console.print("[red]No checkpoint found.[/red]")
        raise typer.Exit(1)
    emitter = build_event_emitter(recording=config.recording.enabled)
    resume_supervisor_turn(
        thread, cp, config, store, retry_failed=retry_failed, events=emitter
    )
    console.print(f"[green]Resumed[/green] turn {cp.turn_id} on thread {thread.id}")


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


@runs_app.command("export")
def runs_export(
    turn_id: str = typer.Argument(..., help="Turn ID (full or prefix)"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output markdown file"),
) -> None:
    """Export run log to Markdown."""
    store = RunStore()
    try:
        events = store.load_events(turn_id, thread_id=thread_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    md = export_run_markdown(events)
    if out:
        out.write_text(md, encoding="utf-8")
        console.print(f"Exported to {out}")
    else:
        stdout_console.print(md)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", help="Bind port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth bearer token"),
    no_control: bool = typer.Option(False, "--no-control", help="Disable cancel API"),
    allow_remote_bind: bool = typer.Option(
        False, "--allow-remote-bind", help="Allow binding to 0.0.0.0"
    ),
) -> None:
    """HTTP dashboard for threads, runs, SSE events, and turn cancel."""
    from agent.serve.server import serve as run_serve

    cfg = load_serve_settings()
    cfg.host = host
    cfg.port = port
    cfg.enable_control = not no_control
    cfg.allow_remote_bind = allow_remote_bind
    if token:
        cfg.auth_token = token
    if host == "0.0.0.0" and not allow_remote_bind:
        console.print(
            "[red]Error:[/red] Refusing 0.0.0.0 without --allow-remote-bind"
        )
        raise typer.Exit(1)
    if host == "0.0.0.0":
        console.print(
            "[yellow]Warning:[/yellow] Binding to 0.0.0.0 exposes the dashboard on all interfaces."
        )
    try:
        run_serve(host=host, port=port, settings=cfg, auth_token=token or cfg.auth_token or None)
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@metrics_app.command("show")
def metrics_show() -> None:
    """Print JSON runtime metrics counters."""
    stdout_console.print(MetricsCollector.global_collector().to_json())


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
