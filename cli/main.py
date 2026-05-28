from __future__ import annotations

import json
import os
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
from agent.loop import run_turn
from agent.mcp.manager import McpManager
from agent.models import Thread, new_id, utc_now_iso
from agent.paths import default_config_path
from agent.export.html import export_thread_html
from agent.export.markdown import export_run_markdown, export_thread_markdown
from agent.export.pr_description import build_pr_description
from agent.execution.docker import check_docker_available, check_docker_hello_world
from agent.execution.factory import backend_display, run_execution_test
from agent.execution.ssh import check_ssh_available, validate_ssh_config
from agent.config_validate import validate_config
from agent.execution.sync.service import (
    format_plan_verbose,
    resolve_sync_path,
    run_sync_fetch_remote,
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
from agent.settings import (
    load_auth_storage_settings,
    load_mcp_config,
    load_schedule_settings,
    load_serve_settings,
    load_skills_config,
)
from agent.profiles import (
    load_model_profiles,
    load_run_profiles,
    merge_layered_config,
    project_config_path,
    thread_cost_summary,
)
from agent.init_scaffold import init_project
from agent.model_routing import resolve_model_profile_from_task
from agent.output_handler import OutputHandler, format_run_summary, stderr_console, stdout_console
from agent.repl import run_repl
from agent.providers.openrouter import ModelsCache, check_openrouter_health, recommend_model, tool_support_warning
from agent.skills.doctor import skills_doctor_report
from tools.registry import TOOL_REGISTRY, get_tool_schemas
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.exec_policy import evaluate_command, should_prompt_for_command
from agent.turn_checkpoint import TurnCheckpointStore
from agent.store import ThreadStore
from agent.tui.runner import check_tui_available, launch_interactive_session

app = typer.Typer(no_args_is_help=False, help="Codex-inspired coding agent CLI")
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
telemetry_app = typer.Typer(help="OpenTelemetry tracing")
auth_app = typer.Typer(help="Auth helpers")
serve_app = typer.Typer(help="HTTP dashboard server", invoke_without_command=True)
serve_users_app = typer.Typer(help="RBAC user management")
serve_oidc_app = typer.Typer(help="OIDC SSO configuration")
auth_sessions_app = typer.Typer(help="Session management")
auth_policy_app = typer.Typer(help="OAuth policy engine")
serve_policy_app = typer.Typer(help="Serve policy status")
serve_webhooks_app = typer.Typer(help="Webhook status")
auth_webhooks_app = typer.Typer(help="OIDC webhook helpers")
schedule_notifications_app = typer.Typer(help="Schedule notification helpers")
schedule_app = typer.Typer(help="Scheduled swarm jobs")
marketplace_app = typer.Typer(help="Signed skill marketplace")
skills_lock_app = typer.Typer(help="Skill lockfile for reproducible installs")
skills_revocations_app = typer.Typer(help="Marketplace revocation list")
multi_agent_budgets_app = typer.Typer(help="Swarm budget tracking")
programs_app = typer.Typer(help="Cross-thread program DAG")
programs_sync_app = typer.Typer(help="Cross-machine program sync")
profile_app = typer.Typer(help="Run profile management")
models_app = typer.Typer(help="OpenRouter model discovery")
tools_app = typer.Typer(help="Built-in and MCP tools")
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
app.add_typer(telemetry_app, name="telemetry")
app.add_typer(auth_app, name="auth")
app.add_typer(serve_app, name="serve")
serve_app.add_typer(serve_users_app, name="users")
serve_app.add_typer(serve_oidc_app, name="oidc")
auth_app.add_typer(auth_sessions_app, name="sessions")
auth_app.add_typer(auth_policy_app, name="policy")
auth_app.add_typer(auth_webhooks_app, name="webhooks")
serve_app.add_typer(serve_policy_app, name="policy")
serve_app.add_typer(serve_webhooks_app, name="webhooks")
app.add_typer(schedule_app, name="schedule")
schedule_app.add_typer(schedule_notifications_app, name="notifications")
skills_app.add_typer(marketplace_app, name="marketplace")
skills_app.add_typer(skills_lock_app, name="lock")
skills_app.add_typer(skills_revocations_app, name="revocations")
multi_agent_app.add_typer(multi_agent_budgets_app, name="budgets")
app.add_typer(programs_app, name="programs")
programs_app.add_typer(programs_sync_app, name="sync")
app.add_typer(profile_app, name="profile")
app.add_typer(models_app, name="models")
hooks_app = typer.Typer(help="Lifecycle hooks")
memories_app = typer.Typer(help="Cross-session memory notes")
app.add_typer(tools_app, name="tools")
app.add_typer(hooks_app, name="hooks")
app.add_typer(memories_app, name="memories")

console = stderr_console


@app.callback(invoke_without_command=True)
def cli_entry(
    ctx: typer.Context,
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id", help="Resume thread"),
    resume_last: bool = typer.Option(False, "--resume-last", help="Resume latest thread"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Named run profile"),
    model_profile: Optional[str] = typer.Option(
        None, "--model-profile", help="Model profile"
    ),
) -> None:
    """Open the interactive agent (default when no subcommand is given)."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        resolved_config = Config.resolve(
            cwd=cwd,
            profile=profile,
            model_profile=model_profile,
        )
        resolved_config.require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    try:
        launch_interactive_session(
            cwd=cwd,
            thread_id=thread_id,
            resume_last=resume_last,
            profile=profile,
            model_profile=model_profile,
            config=resolved_config,
        )
    except SystemExit as exc:
        raise typer.Exit(exc.code) from exc


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
    resume: bool = typer.Option(
        False, "--resume", help="Pick a thread to resume (same cwd filter)"
    ),
    ephemeral: bool = typer.Option(
        False, "--ephemeral", help="Do not persist thread JSONL to the store"
    ),
    remember: bool = typer.Option(
        False, "--remember", help="Suggest saving a memory after a successful turn"
    ),
    max_cost: Optional[float] = typer.Option(
        None, "--max-cost", help="Stop turn when estimated cost exceeds this USD cap"
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
    json_output: bool = typer.Option(
        False, "--json", help="Emit normalized Codex-like JSONL events on stdout"
    ),
    plan: bool = typer.Option(
        False, "--plan", help="Plan mode: restrict tools to read/search/planning only"
    ),
    output_schema: Optional[str] = typer.Option(
        None, "--output-schema", help="JSON schema file or inline JSON for final output"
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
    budget_profile: Optional[str] = typer.Option(
        None,
        "--budget-profile",
        help="Swarm budget profile: strict | standard | off",
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile", help="Named run profile (merges overrides for this run)"
    ),
    model_profile: Optional[str] = typer.Option(
        None, "--model-profile", help="Model profile: fast | deep | custom from config"
    ),
    resume_turn: bool = typer.Option(
        False,
        "--resume-turn",
        help="Resume a cancelled single-agent turn from checkpoint (requires --thread-id or --resume-last)",
    ),
) -> None:
    """Run the agent on a task."""
    routed_profile = resolve_model_profile_from_task(
        prompt,
        cli_model_profile=model_profile,
        cwd=cwd or Path.cwd(),
    )
    effective_model_profile = model_profile or routed_profile
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
            profile=profile,
            model_profile=effective_model_profile,
            max_cost_usd=max_cost,
        )
        config.require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if routed_profile and not model_profile and not jsonl_events and not json_output:
        console.print(f"[dim]Auto-routed model profile:[/dim] {routed_profile}")

    models_cache = ModelsCache().load()
    warn = tool_support_warning(config.model, models_cache or None)
    if warn and not jsonl_events and not json_output:
        console.print(f"[yellow]Warning:[/yellow] {warn}")

    repo_root = detect_repo_root(config.cwd)
    if not skip_git_check and repo_root is None:
        console.print(
            "[yellow]Warning:[/yellow] cwd is not inside a git repository. "
            "Use --skip-git-check to suppress."
        )

    store = ThreadStore.ephemeral() if ephemeral else ThreadStore()
    thread: Thread | None = None

    if ephemeral:
        thread = Thread(
            id=f"ephemeral-{new_id()}",
            cwd=str(config.cwd),
            model=config.model,
            repo_root=repo_root,
            title=title,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
    elif thread_id:
        thread = _load_thread(store, thread_id)
    elif resume_last:
        thread = store.find_latest_for_cwd(str(config.cwd))
        if not thread:
            console.print(
                f"[red]Error:[/red] No previous thread found for cwd: {config.cwd}"
            )
            raise typer.Exit(1)
    elif resume:
        from agent.threads_picker import pick_thread

        thread = pick_thread(store, config.cwd)
        if not thread:
            console.print("[red]Error:[/red] No thread selected")
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

    if ephemeral and not jsonl_events and not json_output:
        console.print("[dim]Ephemeral run — thread will not be saved[/dim]")

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

    machine_output = jsonl_events or json_output
    if session_auto_approve and not machine_output:
        console.print("[dim][approval] session auto-approve enabled[/dim]")

    if not machine_output:
        console.print(f"[dim]Thread:[/dim] {thread.display_label()} ({thread.id})")
        console.print(f"[dim]Model:[/dim] {config.model}")
        console.print(f"[dim]CWD:[/dim] {thread.cwd}")
        console.print(f"[dim]Sandbox:[/dim] {config.sandbox_mode.value}")
        console.print(f"[dim]Execution:[/dim] {backend_display(config)}")
        if config.multi_agent.enabled:
            console.print("[dim]Multi-agent:[/dim] enabled (spawn_worker)")
        if budget_profile:
            console.print(f"[dim]Budget profile:[/dim] {budget_profile}")
        if thread.repo_root:
            console.print(f"[dim]Repo:[/dim] {thread.repo_root}")
        console.print()

    output = OutputHandler(
        jsonl_events=jsonl_events and not json_output,
        json_stream=json_output,
        quiet_tools=quiet_tools,
    )
    emitter = build_event_emitter(
        output.handle,
        recording=config.recording.enabled,
        recording_keep=config.recording.keep_last_runs_per_thread,
        action_log=config.action_log,
        project_cwd=config.cwd,
        model=config.model,
    )
    if json_output:
        emitter.thread_started(thread.id, title=thread.title)

    schema_obj = None
    if output_schema:
        from agent.output_schema import load_output_schema

        schema_obj = load_output_schema(output_schema)

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
        elif resume_turn:
            if not thread:
                console.print("[red]Error:[/red] --resume-turn requires --thread-id or --resume-last")
                raise typer.Exit(1)
            cp_store = TurnCheckpointStore(Path(config.turn_checkpoint.dir).expanduser())
            resume_cp = cp_store.find_latest(thread.id)
            if not resume_cp:
                console.print("[red]Error:[/red] No single-agent turn checkpoint found for this thread.")
                raise typer.Exit(1)
            if not machine_output:
                console.print(
                    f"[dim]Resuming cancelled turn {resume_cp.turn_id[:8]}…[/dim]"
                )
            turn = run_turn(
                thread,
                resume_cp.user_text or prompt,
                config,
                store,
                events=emitter,
                cancel_token=cancel_token,
                quiet_tools=quiet_tools,
                session_auto_approve=session_auto_approve,
                force_sync=force_sync,
                budget_profile=budget_profile,
                resume_checkpoint=resume_cp,
                plan_mode=plan,
                headless_json=json_output,
                output_schema=schema_obj,
                remember=remember,
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
                budget_profile=budget_profile,
                plan_mode=plan,
                headless_json=json_output,
                output_schema=schema_obj,
                remember=remember,
            )
    except CancelledError:
        if not machine_output:
            console.print()
            console.print(f"[yellow]Cancelled.[/yellow] Thread: {thread.id}")
        raise typer.Exit(130) from None
    except Exception as exc:
        console.print()
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        signal.signal(signal.SIGINT, previous)

    if not machine_output:
        from agent.turn_stats import aggregate_turn_stats

        stats = aggregate_turn_stats(turn)
        budget_exceeded = any(
            item.type == "agentMessage" and "max_cost_usd_per_turn" in item.text
            for item in turn.items
        )
        console.print()
        if turn.status == "completed":
            console.print(f"[green]Done.[/green] Thread: {thread.id}")
            console.print(
                format_run_summary(turn, budget_exceeded=budget_exceeded, stats=stats),
                markup=False,
            )
        elif turn.status == "cancelled":
            console.print(f"[yellow]Cancelled.[/yellow] Thread: {thread.id}")
        else:
            console.print(f"[yellow]Turn {turn.status}.[/yellow] Thread: {thread.id}")
            console.print(
                format_run_summary(turn, budget_exceeded=budget_exceeded, stats=stats),
                markup=False,
            )

    if json_output and output.stream_handler:
        from datetime import datetime, timezone

        summary = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": turn.status,
            "model": turn.usage.model_used or config.model,
            "cost": turn.usage.estimated_cost_usd,
            "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens,
        }
        from agent.turn_stats import aggregate_turn_stats

        summary["stats"] = aggregate_turn_stats(turn).to_dict()
        if any(
            item.type == "agentMessage" and "max_cost_usd_per_turn" in item.text
            for item in turn.items
        ):
            summary["budget_exceeded"] = True
        line = output.stream_handler.emit_run_summary(
            thread_id=thread.id, turn_id=turn.id, summary=summary
        )
        stdout_console.print(line)

    if schema_obj and turn.status == "completed":
        from agent.models import AgentMessageItem
        from agent.output_schema import parse_final_output

        final_text = ""
        for item in reversed(turn.items):
            if isinstance(item, AgentMessageItem):
                final_text = item.text
                break
        parsed, errors = parse_final_output(final_text, schema_obj)
        if errors or parsed is None:
            if json_output and output.stream_handler:
                line = output.stream_handler.emit_run_result(
                    thread_id=thread.id,
                    turn_id=turn.id,
                    result={"valid": False, "errors": errors},
                )
                stdout_console.print(line)
            else:
                console.print(f"[red]Output schema validation failed:[/red] {errors}")
            raise typer.Exit(1)
        import json as _json

        if json_output and output.stream_handler:
            line = output.stream_handler.emit_run_result(
                thread_id=thread.id,
                turn_id=turn.id,
                result={"valid": True, "data": parsed},
            )
            stdout_console.print(line)
        else:
            stdout_console.print(_json.dumps(parsed, indent=2))

    if turn.status == "cancelled":
        raise typer.Exit(130)


@app.command("review")
def review_cmd(
    prompt: Optional[str] = typer.Argument(None, help="Custom review focus (use '-' for stdin)"),
    uncommitted: bool = typer.Option(False, "--uncommitted", help="Review uncommitted changes"),
    base: Optional[str] = typer.Option(None, "--base", help="Review diff vs branch (e.g. main)"),
    commit: Optional[str] = typer.Option(None, "--commit", help="Review a specific commit SHA"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory"),
    model: Optional[str] = typer.Option(None, "--model", help="OpenRouter model slug"),
    model_profile: Optional[str] = typer.Option(None, "--model-profile", help="Model profile"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Auto-approve tool prompts"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON report + event stream"),
    fail_on: Optional[str] = typer.Option(
        None,
        "--fail-on",
        help="Exit 1 when findings at/above severities (e.g. critical,major)",
    ),
    severity_threshold: Optional[str] = typer.Option(
        None,
        "--severity-threshold",
        help="Alias for --fail-on",
    ),
    schema_version: str = typer.Option("v1", "--schema-version", help="Review JSON schema version"),
    fix: bool = typer.Option(False, "--fix", help="Allow write tools (not read-only review)"),
    skip_git_check: bool = typer.Option(False, "--skip-git-check"),
) -> None:
    """Run a read-only code review on git changes."""
    modes = sum([uncommitted, base is not None, commit is not None])
    if modes == 0:
        uncommitted = True
    elif modes > 1:
        console.print("[red]Error:[/red] Specify only one of --uncommitted, --base, or --commit")
        raise typer.Exit(1)

    try:
        config = Config.resolve(
            cwd=cwd,
            model=model,
            auto_approve=auto_approve if auto_approve else None,
            skip_git_check=skip_git_check,
            model_profile=model_profile,
        )
        config.require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    from agent.review import collect_review_context
    from agent.review_runner import run_review

    custom_prompt = ""
    if prompt == "-":
        import sys

        custom_prompt = sys.stdin.read().strip()
    elif prompt:
        custom_prompt = prompt

    if uncommitted:
        mode = "uncommitted"
        ctx = collect_review_context(config.cwd, mode=mode, custom_prompt=custom_prompt)
    elif base is not None:
        mode = "base"
        ctx = collect_review_context(config.cwd, mode=mode, base=base, custom_prompt=custom_prompt)
    else:
        mode = "commit"
        ctx = collect_review_context(config.cwd, mode=mode, commit=commit, custom_prompt=custom_prompt)

    output = OutputHandler(json_stream=json_output, quiet_tools=True)
    emitter = build_event_emitter(output.handle)
    if json_output:
        emitter.thread_started("pending", title=ctx.title)

    result = run_review(
        config,
        ctx=ctx,
        events=emitter,
        auto_approve=auto_approve,
        allow_fix=fix,
        json_output=json_output,
        model_profile=model_profile,
    )

    if json_output:
        if output.stream_handler:
            line = output.stream_handler.emit_run_result(
                thread_id=result.thread_id,
                turn_id=result.turn_id,
                result=result.structured or {},
            )
            stdout_console.print(line)
        if result.report_json:
            stdout_console.print(result.report_json)
    else:
        stdout_console.print(result.report_markdown)

    from agent.review import parse_fail_on_severities, review_exceeds_fail_threshold

    threshold = fail_on or severity_threshold
    if threshold and result.report:
        severities = parse_fail_on_severities(threshold)
        if review_exceeds_fail_threshold(result.report, severities):
            raise typer.Exit(1)


@hooks_app.command("list")
def hooks_list(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """List configured lifecycle hooks."""
    from agent.hooks.runner import SUPPORTED_HOOK_EVENTS, load_hooks_config

    root = (cwd or Path.cwd()).resolve()
    hooks = load_hooks_config(root)
    for event in SUPPORTED_HOOK_EVENTS:
        entries = hooks.get(event, [])
        console.print(f"[bold]{event}[/bold] ({len(entries)})")
        for entry in entries:
            console.print(f"  - {entry.get('command', '')}")


@hooks_app.command("test")
def hooks_test(
    event: str = typer.Argument(..., help="Hook event name (e.g. on_tool_pending)"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Run hooks for an event with a sample payload."""
    from agent.hooks.runner import HooksRunner

    root = (cwd or Path.cwd()).resolve()
    runner = HooksRunner.from_cwd(root)
    errors = runner.run(event, {"test": True}, tool_name="read_file")
    if errors:
        console.print(f"[yellow]Hook errors:[/yellow] {errors}")
        raise typer.Exit(1)
    console.print("[green]Hooks OK[/green]")


@memories_app.command("list")
def memories_list_cmd() -> None:
    """List stored memories."""
    from agent.memories import MemoryStore

    for mem in MemoryStore().list_all():
        console.print(f"{mem.id[:8]}  {mem.text[:80]}")


@memories_app.command("add")
def memories_add_cmd(
    text: str = typer.Argument(..., help="Memory text"),
    tag: Optional[str] = typer.Option(None, "--tag"),
) -> None:
    """Add a memory note."""
    from agent.memories import MemoryStore

    tags = [tag] if tag else []
    mem = MemoryStore().add(text, tags=tags)
    console.print(f"Added {mem.id}")


@memories_app.command("delete")
def memories_delete_cmd(memory_id: str = typer.Argument(..., help="Memory id or prefix")) -> None:
    """Delete a memory by id."""
    from agent.memories import MemoryStore

    store = MemoryStore()
    for mem in store.list_all():
        if mem.id == memory_id or mem.id.startswith(memory_id):
            if store.delete(mem.id):
                console.print(f"Deleted {mem.id}")
                return
    console.print("[red]Not found[/red]")
    raise typer.Exit(1)


@memories_app.command("search")
def memories_search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Boost memories from this cwd"),
) -> None:
    """Search memories by keyword overlap, recency, and cwd."""
    from agent.memories import MemoryStore

    store = MemoryStore()
    cwd_str = str((cwd or Path.cwd()).resolve())
    for entry in store.search_scored(query, cwd=cwd_str)[:10]:
        mem = entry.memory
        console.print(f"{mem.id[:8]}  score={entry.score:.1f}  {mem.text}")


@memories_app.command("suggest")
def memories_suggest_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
) -> None:
    """List pending memory suggestions."""
    from agent.memories import SuggestQueue

    root = (cwd or Path.cwd()).resolve()
    pending = SuggestQueue(root).list_pending()
    if not pending:
        console.print("No pending suggestions.")
        return
    for mem in pending:
        console.print(f"{mem.id[:8]}  {mem.text[:120]}")


@memories_app.command("accept")
def memories_accept_cmd(
    memory_id: str = typer.Argument(..., help="Suggestion id or prefix"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Accept a pending memory suggestion."""
    from agent.memories import MemoryStore, SuggestQueue

    root = (cwd or Path.cwd()).resolve()
    queue = SuggestQueue(root)
    store = MemoryStore()
    for mem in queue.list_pending():
        if mem.id == memory_id or mem.id.startswith(memory_id):
            accepted = queue.accept(mem.id, store=store)
            if accepted:
                console.print(f"Accepted {accepted.id}")
                return
    console.print("[red]Not found[/red]")
    raise typer.Exit(1)


@memories_app.command("reject")
def memories_reject_cmd(
    memory_id: str = typer.Argument(..., help="Suggestion id or prefix"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Reject a pending memory suggestion."""
    from agent.memories import SuggestQueue

    root = (cwd or Path.cwd()).resolve()
    queue = SuggestQueue(root)
    for mem in queue.list_pending():
        if mem.id == memory_id or mem.id.startswith(memory_id):
            if queue.reject(mem.id):
                console.print(f"Rejected {mem.id}")
                return
    console.print("[red]Not found[/red]")
    raise typer.Exit(1)


@memories_app.command("inject")
def memories_inject_cmd(
    prompt: str = typer.Argument(..., help="Prompt text to match memories against"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview inject block without writing"),
) -> None:
    """Preview or show memory injection block for a prompt."""
    from agent.memories import inject_memories_dry_run, inject_memories_prompt

    cfg = Config.resolve(cwd=cwd)
    cwd_str = str(cfg.cwd)
    if dry_run:
        stdout_console.print(inject_memories_dry_run(prompt, settings=cfg.memories, cwd=cwd_str))
        return
    stdout_console.print(
        inject_memories_prompt(prompt, settings=cfg.memories, cwd=cwd_str) or "(no matching memories)"
    )


@config_app.command("validate")
def config_validate_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
) -> None:
    """Validate configuration for known issues and dangerous combinations."""
    result = validate_config(Config.resolve(cwd=cwd))
    for issue in result.issues:
        color = "red" if issue.level == "error" else "yellow"
        console.print(f"[{color}]{issue.level}:[/{color}] {issue.message}")
    code = result.exit_code(strict=strict)
    if code == 0:
        console.print("[green]Configuration OK[/green]")
    raise typer.Exit(code)


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
    models: bool = typer.Option(False, "--models", help="Show model preflight table for profiles"),
    json_report: bool = typer.Option(False, "--json", help="Machine-readable harness diagnostics"),
) -> None:
    """Check environment: API key, git, ripgrep, node/npx, skills, config."""
    cfg = Config.resolve()
    if json_report:
        from agent.doctor.report import build_doctor_report

        report = build_doctor_report(cfg)
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        if not cfg.openrouter_api_key:
            raise typer.Exit(1)
        return

    config_path = default_config_path()
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
    from agent.execution.shell_session import pty_support_status

    pty = pty_support_status()
    table.add_row(
        "persistent shell / PTY",
        f"enabled={cfg.shell.enabled}, configured={cfg.shell.backend}, "
        f"probe={pty.get('backend')}, conpty={pty.get('conpty_available')}, "
        f"max_output={cfg.shell.max_output_chars}, yield_ms={cfg.shell.default_yield_ms}, "
        f"{pty.get('note', '')}",
    )
    ws = cfg.web_search
    ws_key = "set" if (ws.api_key or (ws.api_key_env and os.environ.get(ws.api_key_env))) else "missing"
    if ws.provider == "duckduckgo":
        ws_key = "n/a"
    table.add_row("web search", f"provider={ws.provider} key={ws_key}")
    table.add_row(
        "harness",
        f"max_parallel_read_tools={cfg.harness.max_parallel_read_tools}",
    )
    table.add_row(
        "multi-agent",
        f"enabled={cfg.multi_agent.enabled}, max_workers={cfg.multi_agent.max_workers_per_turn}, "
        f"max_depth={cfg.multi_agent.max_worker_depth}, max_concurrent={cfg.multi_agent.max_concurrent_workers}, "
        f"dag={cfg.multi_agent.dag_enabled}",
    )
    table.add_row(
        "telemetry",
        f"enabled={cfg.telemetry.enabled}, service={cfg.telemetry.service_name}, "
        f"sample_rate={cfg.telemetry.sample_rate}",
    )
    try:
        import opentelemetry  # noqa: F401

        otel_pkgs = "installed"
    except ImportError:
        otel_pkgs = "not installed (pip install -e '.[otel]')"
    table.add_row("OpenTelemetry packages", otel_pkgs)
    from agent.sandbox.profiles import select_profile

    sp = cfg.sandbox_profiles
    prof = select_profile(sp)
    prof_avail = "available" if prof.available() else "unavailable (fail_open)"
    table.add_row(
        "sandbox profiles",
        f"enabled={sp.enabled}, profile={sp.profile}, selected={prof.name}, {prof_avail}",
    )
    from agent.sandbox.kernel.doctor import probe_capabilities

    kcap = probe_capabilities(cfg.sandbox_kernel)
    table.add_row(
        "kernel sandbox",
        f"enabled={cfg.sandbox_kernel.enabled}, backend={kcap.get('backend')}, "
        f"available={kcap.get('available')}, fail_open={cfg.sandbox_kernel.fail_open}",
    )
    if kcap.get("appcontainer"):
        table.add_row(
            "AppContainer",
            f"{kcap.get('appcontainer')} ({kcap.get('appcontainer_reason', '')})",
        )
    if cfg.multi_agent.cross_thread.enabled and not cfg.multi_agent.budgets.enabled:
        checks.append(
            (
                "budget warning",
                "cross_thread enabled without swarm budgets — enable [multi_agent.budgets] for production",
            )
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
    serve_cfg = load_serve_settings(cfg.config_path)
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
    if serve_cfg.allow_remote_bind and not serve_cfg.auth_token and not serve_cfg.rbac.enabled:
        checks.append(
            (
                "serve warning",
                "allow_remote_bind without auth_token — set serve.auth_token or enable RBAC",
            )
        )
    if serve_cfg.enable_turn_start and serve_cfg.host not in ("127.0.0.1", "localhost"):
        checks.append(
            (
                "serve warning",
                "enable_turn_start with non-localhost bind — use token and firewall",
            )
        )
    table.add_row(
        "serve",
        f"auth_mode={serve_cfg.auth_mode}, rbac={serve_cfg.rbac.enabled}, tls={serve_cfg.tls.enabled}, "
        f"control={serve_cfg.enable_control}, turn_start={serve_cfg.enable_turn_start}",
    )
    table.add_row(
        "auth policy",
        f"enabled={serve_cfg.policy.enabled}, rules={len(serve_cfg.policy.rules)}, "
        f"introspection={'yes' if serve_cfg.policy.introspection_url else 'no'}",
    )
    sched_cfg = load_schedule_settings(config_path)
    table.add_row(
        "scheduler",
        f"enabled={sched_cfg.enabled}, require_budgets={sched_cfg.require_budgets}, "
        f"approval={sched_cfg.default_approval_mode}",
    )
    if serve_cfg.ide.enabled:
        diag = serve_cfg.ide.diagnostics
        table.add_row(
            "IDE diagnostics",
            f"enabled={diag.enabled}, python={diag.python_tool}, js={diag.js_tool}, "
            f"ruff={'found' if shutil.which('ruff') else 'missing'}, "
            f"eslint={'found' if shutil.which('eslint') else 'missing'}",
        )
    ct = cfg.multi_agent.cross_thread
    table.add_row(
        "program sync",
        f"enabled={ct.sync_enabled}, backend={ct.sync_backend}, sign={ct.sign_program_state}",
    )
    table.add_row(
        "auth webhooks",
        f"enabled={serve_cfg.webhooks.enabled}, path={serve_cfg.webhooks.path}",
    )
    sched_cfg = load_schedule_settings(config_path)
    table.add_row(
        "schedule notifications",
        f"enabled={sched_cfg.notifications.enabled}, url={'set' if sched_cfg.notifications.webhook_url else 'log-only'}",
    )
    oidc = serve_cfg.oidc
    if oidc and oidc.enabled:
        table.add_row(
            "OIDC SSO",
            f"issuer={oidc.issuer_url[:40]}..., tls={'ok' if serve_cfg.tls.enabled else 'REQUIRED'}, "
            f"rbac={'ok' if serve_cfg.rbac.enabled else 'warn: disabled'}",
        )
        if not serve_cfg.tls.enabled:
            checks.append(("OIDC error", "OIDC requires TLS — enable [serve.tls] or terminate at proxy"))
        if not serve_cfg.rbac.enabled:
            checks.append(("OIDC warning", "OIDC enabled without RBAC — roles will not be enforced"))
    elif "oidc" in (serve_cfg.auth_mode or "").lower():
        table.add_row("OIDC SSO", "auth_mode includes oidc but issuer not configured")
    table.add_row(
        "telemetry v2",
        f"histograms={cfg.telemetry.export_runtime_metrics}, buckets={len(cfg.telemetry.histogram_buckets_sec)}",
    )
    skills_cfg = load_skills_config(config_path)
    mp = skills_cfg.marketplace
    from agent.skills.marketplace import marketplace_dir

    key_count = len(list((marketplace_dir(mp.registry_dir) / "keys").glob("*.pub")))
    table.add_row(
        "skill marketplace",
        f"enabled={mp.enabled}, trusted_keys={key_count}, require_signature={mp.require_signature}, "
        f"remote={'yes' if mp.remote_registry_url else 'no'}",
    )
    if mp.remote_registry_url:
        from agent.skills.marketplace_remote import load_sync_state, marketplace_dir

        sync = load_sync_state(marketplace_dir(mp.registry_dir))
        last = (
            f"{int(__import__('time').time() - sync.last_sync_at)}s ago"
            if sync.last_sync_at
            else "never"
        )
        table.add_row("marketplace sync", f"last={last}, version={sync.registry_version}")
    from agent.auth.oidc_tokens import OidcTokenStore
    from agent.auth.storage import keyring_available

    storage = load_auth_storage_settings(config_path)
    backend = OidcTokenStore(storage_settings=storage).backend_name
    table.add_row(
        "auth storage",
        f"backend={backend}, keyring={'available' if keyring_available() else 'missing (file fallback)'}",
    )
    if not keyring_available() and OidcTokenStore(storage_settings=storage).load():
        checks.append(
            (
                "auth warning",
                "keyring not installed — OIDC tokens stored in ~/.agent-cli/auth/oidc.json (0600)",
            )
        )
    budgets = cfg.multi_agent.budgets
    table.add_row(
        "swarm budgets",
        f"enabled={budgets.enabled}, max_workers={budgets.max_workers_spawned}, "
        f"on_exceed={budgets.on_budget_exceeded}",
    )
    if cfg.multi_agent.enabled and not budgets.enabled:
        checks.append(
            (
                "budget warning",
                "multi_agent enabled but budgets disabled — consider [multi_agent.budgets] enabled=true",
            )
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

    solo = Table(title="Solo dev readiness")
    solo.add_column("Check")
    solo.add_column("Status")
    project_cfg = project_config_path(cfg.cwd)
    project_cfg_ok = "valid" if project_cfg.is_file() else "run `agent init`"
    if project_cfg.is_file():
        try:
            validate_config(Config.resolve(cwd=cfg.cwd))
            project_cfg_ok = "valid"
        except Exception:
            project_cfg_ok = "invalid TOML or resolve error"
    solo_checks = [
        ("OPENROUTER_API_KEY", "ok" if api_key else "MISSING"),
        ("git repository", "ok" if detect_repo_root(cfg.cwd) else "not in git repo"),
        ("ripgrep", "ok" if rg_ok else "fallback search"),
        ("project config", project_cfg_ok),
        ("model fallbacks", str(len(cfg.openrouter.fallback_models))),
        ("default pricing", "seeded" if cfg.openrouter.pricing else "empty"),
    ]
    try:
        from agent.providers.openrouter import ModelsCache as MC

        cache = MC()
        cached_models = cache.load()
        if cached_models:
            solo_checks.append(("models cache", f"{len(cached_models)} models"))
        else:
            solo_checks.append(("models cache", "empty (run agent models list)"))
    except Exception:
        cached_models = []
        solo_checks.append(("models cache", "unavailable"))
    tool_warn = tool_support_warning(cfg.model, cached_models or None)
    if tool_warn:
        solo_checks.append(("model tool support", "check model"))
    else:
        solo_checks.append(("model tool support", "ok"))
    from agent.providers.openrouter import probe_openrouter_reachability

    or_health = check_openrouter_health(api_key)
    solo_checks.append(("OpenRouter reachability", or_health.reachability))
    solo_checks.append(("OpenRouter API key", or_health.key_validity))
    if or_health.detail:
        solo_checks.append(("OpenRouter detail", or_health.detail[:80]))
    for name, status in solo_checks:
        style = "green" if status in ("ok",) or status.endswith("models") else "yellow"
        if status == "MISSING":
            style = "red"
        solo.add_row(name, f"[{style}]{status}[/{style}]")

    console.print(table)
    console.print(solo)

    if models:
        from agent.profiles import load_model_profiles
        from agent.providers.openrouter import ModelsCache, build_model_preflight_rows

        cached_models = ModelsCache().load()
        profiles = {
            **load_model_profiles(default_config_path()),
            **load_model_profiles(project_config_path(cfg.cwd)),
        }
        model_table = Table(title="Model preflight")
        model_table.add_column("Profile")
        model_table.add_column("Model")
        model_table.add_column("Tools")
        model_table.add_column("Context")
        model_table.add_column("In $/1M")
        model_table.add_column("Out $/1M")
        model_table.add_column("Vision")
        model_table.add_column("Reasoning")
        for row in build_model_preflight_rows(
            profiles=profiles,
            default_model=cfg.model,
            models=cached_models or None,
            pricing=cfg.openrouter.pricing,
        ):
            model_table.add_row(
                row["profile"],
                row["model"],
                row["tools"],
                row["context"],
                row["input_$1m"],
                row["output_$1m"],
                row["vision"],
                row["reasoning"],
            )
        console.print(model_table)

    if deep:
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

        if serve_cfg.tls.enabled:
            from agent.serve.tls import cert_expiry, expand_path

            cert = expand_path(serve_cfg.tls.cert_file)
            if cert.is_file():
                expiry = cert_expiry(cert)
                if expiry:
                    console.print(f"[dim]TLS cert expires:[/dim] {expiry.isoformat()}")
            else:
                console.print("[yellow]TLS enabled but cert file missing[/yellow]")

        if mp.enabled and key_count == 0:
            console.print("[yellow]Marketplace enabled but no trusted publisher keys found[/yellow]")

        if cfg.telemetry.enabled:
            console.print(
                f"[dim]Telemetry histogram export:[/dim] "
                f"{'on' if cfg.telemetry.export_runtime_metrics else 'off'}"
            )

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


@auth_app.command("login")
def auth_login_device(
    device: bool = typer.Option(False, "--device", help="OAuth device code flow"),
    headless: bool = typer.Option(False, "--headless", help="Non-interactive poll only"),
    timeout: int = typer.Option(600, "--timeout", help="Poll timeout seconds"),
) -> None:
    """Login via OIDC device code (headless CI). Stores tokens in ~/.agent-cli/auth/oidc.json."""
    if not device:
        console.print("[red]Use --device for device code login[/red]")
        raise typer.Exit(1)
    cfg = load_serve_settings()
    if not cfg.oidc or not cfg.oidc.device_code_enabled:
        console.print("[red]device_code_enabled not set in serve.auth.oidc config[/red]")
        raise typer.Exit(1)
    from agent.auth.oidc_tokens import OidcTokenRecord, OidcTokenStore
    from agent.serve.oidc import OidcClient
    import time as _time

    storage = load_auth_storage_settings()
    client = OidcClient(cfg.oidc)
    try:
        flow = client.start_device_flow()
    except Exception as exc:
        console.print(f"[red]Device flow start failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Visit:[/green] {flow.verification_uri_complete or flow.verification_uri}")
    console.print(f"[green]User code:[/green] {flow.user_code}")
    if headless:
        console.print("[dim]Headless mode — poll until timeout[/dim]")
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        status, token_data = client.poll_device_token(flow.device_code)
        if status == "success" and token_data:
            claims = client.claims_from_token_response(token_data)
            principal = client.principal_from_claims(claims)
            rm = cfg.oidc.role_mapping
            email = str(claims.get(rm.claim_email_key, ""))
            record = OidcTokenRecord(
                issuer_url=cfg.oidc.issuer_url,
                client_id=client.device_client_id(),
                access_token=str(token_data.get("access_token", "")),
                refresh_token=str(token_data.get("refresh_token", "")),
                id_token=str(token_data.get("id_token", "")),
                expires_at=_time.time() + float(token_data.get("expires_in", 3600)),
                subject=str(claims.get("sub", "")),
                email=email,
                role=principal.role,
                scopes=list(cfg.oidc.scopes),
            )
            store = OidcTokenStore(storage_settings=storage)
            store.save(record)
            if store.backend_name == "file" and not OidcTokenStore.keyring_available():
                console.print(
                    "[yellow]Warning:[/yellow] keyring not installed — tokens stored as plain JSON"
                )
            console.print(f"[green]Logged in as[/green] {email or principal.name} ({principal.role})")
            return
        if status == "expired":
            console.print("[red]Device code expired[/red]")
            raise typer.Exit(1)
        if status == "error":
            console.print("[red]Device authorization failed[/red]")
            raise typer.Exit(1)
        _time.sleep(flow.interval)
    console.print("[red]Timeout waiting for device authorization[/red]")
    raise typer.Exit(1)


@auth_app.command("status")
def auth_status() -> None:
    """Show OIDC token storage status (tokens redacted)."""
    import json
    import time as _time

    from agent.auth.oidc_tokens import OidcTokenStore
    from agent.auth.refresh import ensure_fresh_tokens, needs_refresh

    cfg = load_serve_settings()
    storage = load_auth_storage_settings()
    store = OidcTokenStore(storage_settings=storage)
    record = ensure_fresh_tokens(store, cfg.oidc) if cfg.oidc else store.load()
    if not record:
        stdout_console.print(json.dumps({"authenticated": False, "backend": store.backend_name}, indent=2))
        return
    summary = record.redacted_summary()
    summary["authenticated"] = True
    summary["backend"] = store.backend_name
    summary["needs_refresh"] = needs_refresh(record, skew_sec=getattr(cfg.oidc, "refresh_skew_sec", 300))
    summary["expires_in_sec"] = max(0, int(record.expires_at - _time.time())) if record.expires_at else None
    stdout_console.print(json.dumps(summary, indent=2))


@auth_app.command("logout")
def auth_logout() -> None:
    """Clear stored OIDC tokens."""
    from agent.auth.oidc_tokens import OidcTokenStore

    storage = load_auth_storage_settings()
    OidcTokenStore(storage_settings=storage).clear()
    console.print("[green]Logged out[/green] — OIDC tokens cleared")


@auth_app.command("refresh")
def auth_refresh(
    force: bool = typer.Option(False, "--force", help="Refresh even if access token is still valid"),
) -> None:
    """Refresh OIDC access token using stored refresh token."""
    import json

    from agent.auth.oidc_tokens import OidcTokenStore
    from agent.auth.refresh import RefreshError, refresh_tokens

    cfg = load_serve_settings()
    if not cfg.oidc:
        console.print("[red]OIDC not configured[/red]")
        raise typer.Exit(1)
    storage = load_auth_storage_settings()
    store = OidcTokenStore(storage_settings=storage)
    try:
        record = refresh_tokens(store, cfg.oidc, force=force)
    except RefreshError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    stdout_console.print(json.dumps(record.redacted_summary(), indent=2))
    console.print("[green]Token refreshed[/green]")


@auth_app.command("hash-token")
def auth_hash_token(
    token: str = typer.Argument(..., help="Plain token to hash for RBAC config"),
) -> None:
    """Print sha256 token hash for serve.rbac.users config."""
    from agent.serve.rbac import hash_token

    stdout_console.print(hash_token(token))


@serve_users_app.command("list")
def serve_users_list() -> None:
    """List configured RBAC users."""
    from agent.serve.users import list_users

    cfg = load_serve_settings()
    for row in list_users(cfg.rbac.users):
        stdout_console.print(f"{row['name']}\t{row['role']}\t{row['token_hash']}")


@serve_users_app.command("add")
def serve_users_add(
    name: str = typer.Argument(...),
    token: str = typer.Argument(..., help="Plain token (stored as hash only)"),
    role: str = typer.Option("viewer", "--role", help="viewer|operator|admin"),
) -> None:
    """Add or update an RBAC user."""
    from agent.serve.users import add_user

    cfg = load_serve_settings()
    result = add_user(name, token, role, cfg.rbac.users)
    console.print(f"[green]User added:[/green] {result['name']} ({result['role']})")


@serve_users_app.command("revoke")
def serve_users_revoke(name: str = typer.Argument(...)) -> None:
    """Revoke a dynamic RBAC user."""
    from agent.serve.users import revoke_user

    cfg = load_serve_settings()
    revoke_user(name, cfg.rbac.users)
    console.print(f"[green]Revoked user[/green] {name}")


@serve_oidc_app.command("configure")
def serve_oidc_configure(
    issuer: str = typer.Option(..., "--issuer", help="OIDC issuer URL"),
    client_id: str = typer.Option(..., "--client-id"),
    redirect_uri: str = typer.Option(
        "https://127.0.0.1:8765/auth/oidc/callback", "--redirect-uri"
    ),
) -> None:
    """Print TOML snippet for OIDC configuration."""
    snippet = f"""
[serve]
auth_mode = "oidc+bearer"

[serve.auth.oidc]
issuer_url = "{issuer}"
client_id = "{client_id}"
redirect_uri = "{redirect_uri}"
pkce = true

[serve.auth.oidc.role_mapping]
default_role = "viewer"
admin_groups = ["agent-admins"]
operator_groups = ["agent-operators"]
"""
    stdout_console.print(snippet.strip())


@serve_oidc_app.command("test-login")
def serve_oidc_test_login() -> None:
    """Validate OIDC settings and print authorize URL (no network)."""
    cfg = load_serve_settings()
    if not cfg.oidc or not cfg.oidc.issuer_url:
        console.print("[red]OIDC not configured in serve settings[/red]")
        raise typer.Exit(1)
    from agent.serve.oidc import OidcClient

    client = OidcClient(cfg.oidc)
    url, state = client.start_login()
    console.print(f"[green]Authorize URL (state={state.state[:8]}...):[/green]")
    stdout_console.print(url)


@auth_sessions_app.command("list")
def auth_sessions_list() -> None:
    """List active serve sessions."""
    from agent.serve.sessions import SessionStore

    store = SessionStore.global_store()
    sessions = store.list_sessions()
    if not sessions:
        console.print("No active sessions.")
        return
    table = Table(title="Sessions")
    table.add_column("ID")
    table.add_column("User")
    table.add_column("Role")
    table.add_column("Method")
    for s in sessions:
        table.add_row(s.session_id[:12] + "...", s.email or s.principal_name, s.role, s.auth_method)
    console.print(table)


@auth_sessions_app.command("revoke")
def auth_sessions_revoke(session_id: str = typer.Argument(...)) -> None:
    """Revoke a session by id prefix or full id."""
    from agent.auth.policy.sessions import SessionRevocationRegistry
    from agent.serve.sessions import SessionStore

    store = SessionStore.global_store()
    registry = SessionRevocationRegistry()
    revoked_id: str | None = None
    if store.revoke_session(session_id):
        revoked_id = session_id
    else:
        for rec in store.list_sessions():
            if rec.session_id.startswith(session_id):
                store.revoke_session(rec.session_id)
                revoked_id = rec.session_id
                break
    if revoked_id:
        registry.revoke(revoked_id, reason="cli-revoke")
        MetricsCollector.global_collector().inc("agent_auth_session_revoked_total")
        console.print(f"[green]Revoked[/green] {revoked_id}")
        return
    console.print("[red]Session not found[/red]")
    raise typer.Exit(1)


@auth_sessions_app.command("revoke-all")
def auth_sessions_revoke_all(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
    except_current: bool = typer.Option(False, "--except-current", help="Keep current CLI session"),
) -> None:
    """Revoke all sessions (registry + in-memory store)."""
    from agent.auth.policy.sessions import SessionRevocationRegistry
    from agent.serve.sessions import SessionStore

    store = SessionStore.global_store()
    sessions = store.list_sessions()
    if not sessions:
        console.print("No active sessions.")
        return
    if not yes:
        console.print(f"Will revoke {len(sessions)} session(s). Re-run with --yes.")
        raise typer.Exit(1)
    keep: str | None = None
    registry = SessionRevocationRegistry()
    ids = [s.session_id for s in sessions]
    n = registry.revoke_all_except(keep, ids, reason="revoke-all")
    for sid in ids:
        if keep and sid == keep:
            continue
        store.revoke_session(sid)
    MetricsCollector.global_collector().inc("agent_auth_session_revoked_total", n)
    console.print(f"[green]Revoked {n} session(s)[/green]")


@auth_policy_app.command("test")
def auth_policy_test(
    role: str = typer.Option("operator", "--role"),
    action: str = typer.Option("thread.run", "--action"),
    mfa: bool = typer.Option(False, "--mfa", help="Simulate MFA claim in amr"),
) -> None:
    """Evaluate policy rules for a role/action pair."""
    import json

    from agent.auth.policy.engine import PolicyContext, evaluate
    from agent.settings import load_serve_settings

    cfg = load_serve_settings()
    claims: dict = {}
    if mfa:
        claims["amr"] = ["mfa"]
    result = evaluate(
        PolicyContext(role=role, action=action, claims=claims, https=True),
        cfg.policy,
    )
    stdout_console.print(json.dumps({"decision": result.decision, "message": result.message, "rule": result.rule}, indent=2))


@serve_policy_app.command("status")
def serve_policy_status() -> None:
    """Show OAuth policy engine configuration."""
    import json

    from agent.auth.policy.sessions import SessionRevocationRegistry
    from agent.settings import load_serve_settings

    cfg = load_serve_settings()
    pol = cfg.policy
    reg = SessionRevocationRegistry()
    stdout_console.print(
        json.dumps(
            {
                "enabled": pol.enabled,
                "require_https": pol.require_https,
                "introspection_url": bool(pol.introspection_url),
                "rules": [r.name for r in pol.rules],
                "revoked_sessions": len(reg.list_revoked()),
            },
            indent=2,
        )
    )


@schedule_app.command("list")
def schedule_list() -> None:
    """List scheduled jobs."""
    from agent.schedule.store import ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    path = Path(sched.state_file).expanduser()
    store = ScheduleStore(path)
    jobs = store.list_jobs()
    if not jobs:
        console.print("No scheduled jobs.")
        return
    table = Table(title="Scheduled jobs")
    table.add_column("ID")
    table.add_column("Cron")
    table.add_column("Enabled")
    table.add_column("Budget")
    for j in jobs:
        table.add_row(j.id, j.cron, str(j.enabled), j.budget_profile)
    console.print(table)


@schedule_app.command("add")
def schedule_add(
    job_id: str = typer.Argument(...),
    cron: str = typer.Option("0 * * * *", "--cron"),
    cwd: str = typer.Option(".", "--cwd"),
    prompt: str = typer.Option("", "--prompt"),
    budget_profile: str = typer.Option("strict", "--budget-profile"),
) -> None:
    from agent.schedule.store import ScheduleJob, ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    store = ScheduleStore(Path(sched.state_file).expanduser())
    job = ScheduleJob(
        id=job_id,
        cron=cron,
        cwd=cwd,
        prompt=prompt or f"Scheduled job {job_id}",
        budget_profile=budget_profile,
    )
    store.add(job)
    console.print(f"[green]Added job[/green] {job.id}")


@schedule_app.command("remove")
def schedule_remove(job_id: str = typer.Argument(...)) -> None:
    from agent.schedule.store import ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    store = ScheduleStore(Path(sched.state_file).expanduser())
    if store.remove(job_id):
        console.print(f"[green]Removed[/green] {job_id}")
    else:
        console.print("[red]Job not found[/red]")
        raise typer.Exit(1)


@schedule_app.command("enable")
def schedule_enable(job_id: str = typer.Argument(...)) -> None:
    from agent.schedule.store import ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    store = ScheduleStore(Path(sched.state_file).expanduser())
    job = store.get(job_id)
    if not job:
        console.print("[red]Job not found[/red]")
        raise typer.Exit(1)
    job.enabled = True
    store.add(job)
    console.print(f"[green]Enabled[/green] {job_id}")


@schedule_app.command("disable")
def schedule_disable(job_id: str = typer.Argument(...)) -> None:
    from agent.schedule.store import ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    store = ScheduleStore(Path(sched.state_file).expanduser())
    job = store.get(job_id)
    if not job:
        console.print("[red]Job not found[/red]")
        raise typer.Exit(1)
    job.enabled = False
    store.add(job)
    console.print(f"[green]Disabled[/green] {job_id}")


@schedule_app.command("tick")
def schedule_tick(
    force: bool = typer.Option(False, "--force", help="Steal stale tick lock"),
) -> None:
    """Run due scheduled jobs once (for Windows Task Scheduler)."""
    import json

    from agent.events import EventEmitter
    from agent.schedule.lock import acquire_tick_lock, release_tick_lock
    from agent.schedule.runner import run_due_jobs
    from agent.schedule.store import ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    if not sched.enabled:
        console.print("[yellow]schedule.enabled=false — nothing to do[/yellow]")
        raise typer.Exit(0)
    acquired, msg = acquire_tick_lock(force=force)
    if not acquired:
        console.print(f"[yellow]skipped: {msg}[/yellow]")
        raise typer.Exit(0)
    try:
        store = ScheduleStore(Path(sched.state_file).expanduser())
        results = run_due_jobs(store, sched, config=Config.resolve(), emitter=EventEmitter())
        stdout_console.print(json.dumps(results, indent=2))
    finally:
        release_tick_lock()


@schedule_app.command("run")
def schedule_run(
    job_id: str = typer.Argument(...),
    now: bool = typer.Option(True, "--now", help="Run immediately"),
) -> None:
    import json

    from agent.events import EventEmitter
    from agent.schedule.runner import run_due_jobs
    from agent.schedule.store import ScheduleStore
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    sched.enabled = True
    store = ScheduleStore(Path(sched.state_file).expanduser())
    results = run_due_jobs(
        store,
        sched,
        config=Config.resolve(),
        emitter=EventEmitter(),
        force_job_id=job_id,
    )
    stdout_console.print(json.dumps(results, indent=2))


@schedule_app.command("history")
def schedule_history(
    job_id: str | None = typer.Option(None, "--job-id"),
) -> None:
    import json

    from agent.schedule.store import list_run_history

    stdout_console.print(json.dumps(list_run_history(job_id), indent=2))


@marketplace_app.command("list")
def marketplace_list(
    remote: bool = typer.Option(False, "--remote", help="Show remote-synced registry entries"),
) -> None:
    """List curated marketplace registry entries."""
    from agent.skills.marketplace import list_marketplace
    from agent.skills.marketplace_remote import load_local_registry

    cfg = Config.resolve()
    skills_cfg = load_skills_config(cfg.config_path)
    mp = skills_cfg.marketplace
    entries = load_local_registry(mp) if remote else list_marketplace(mp)
    if not entries:
        console.print("Marketplace registry empty.")
        return
    table = Table(title="Marketplace" + (" (remote)" if remote else ""))
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Publisher")
    for e in entries:
        table.add_row(str(e.get("name", "")), str(e.get("version", "")), str(e.get("publisher", "")))
    console.print(table)


@marketplace_app.command("sync")
def marketplace_sync(
    force: bool = typer.Option(False, "--force", help="Bypass sync interval cache"),
) -> None:
    """Download and verify remote skill registry + revocations."""
    import json

    from agent.events import EventEmitter
    from agent.skills.marketplace_remote import sync_remote_registry, sync_revocations

    cfg = Config.resolve()
    mp = load_skills_config(cfg.config_path).marketplace
    emitter = EventEmitter(lambda e: None)
    reg = sync_remote_registry(mp, force=force)
    rev = sync_revocations(mp, force=force)
    if reg.get("ok"):
        emitter.skills_marketplace_synced(skills=reg.get("skills", 0), cached=reg.get("cached", False))
    stdout_console.print(json.dumps({"registry": reg, "revocations": rev}, indent=2))
    if not reg.get("ok"):
        raise typer.Exit(1)


@skills_app.command("install")
def skills_install(
    target: str = typer.Argument(..., help="name@version"),
    from_registry: bool = typer.Option(
        False, "--from-registry", help="Install from synced marketplace registry"
    ),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Install a skill from the marketplace registry."""
    import json

    from agent.events import EventEmitter
    from agent.skills.marketplace_remote import install_from_registry

    if not from_registry:
        console.print("[red]Use --from-registry or agent skills marketplace install[/red]")
        raise typer.Exit(1)
    if "@" not in target:
        console.print("[red]Expected name@version[/red]")
        raise typer.Exit(1)
    name, version = target.split("@", 1)
    cfg = Config.resolve(cwd=cwd)
    mp = load_skills_config(cfg.config_path).marketplace
    emitter = EventEmitter(lambda e: None)
    result = install_from_registry(name, version, settings=mp, cwd=cfg.cwd, emitter=emitter)
    if result.get("revoked"):
        emitter.skills_marketplace_revocation_blocked(
            name=name, version=version, reason=str(result.get("error", ""))
        )
    stdout_console.print(json.dumps(result, indent=2, default=str))
    if not result.get("ok"):
        raise typer.Exit(1)


@skills_revocations_app.command("check")
def skills_revocations_check() -> None:
    """Check cached skill revocation list."""
    import json

    from agent.skills.marketplace_remote import load_revocations, sync_revocations

    mp = load_skills_config().marketplace
    sync_revocations(mp)
    items = load_revocations(mp)
    stdout_console.print(json.dumps({"count": len(items), "revocations": items}, indent=2))


@skills_lock_app.command("update")
def skills_lock_update(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Write .agent-cli/skills.lock.json from installed registry entries."""
    import json

    from agent.skills.marketplace_remote import default_lockfile_path, load_local_registry, write_lockfile

    cfg = Config.resolve(cwd=cwd)
    mp = load_skills_config(cfg.config_path).marketplace
    entries = []
    for item in load_local_registry(mp):
        entries.append(
            {
                "name": item.get("name"),
                "version": item.get("version"),
                "sha256": item.get("sha256", ""),
                "publisher": item.get("publisher", ""),
            }
        )
    path = write_lockfile(entries, cfg.cwd)
    stdout_console.print(json.dumps({"ok": True, "path": str(path), "skills": len(entries)}, indent=2))


@skills_lock_app.command("verify")
def skills_lock_verify(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Verify skills.lock.json sha256 pins."""
    import json

    from agent.skills.marketplace_remote import verify_lockfile

    cfg = Config.resolve(cwd=cwd)
    result = verify_lockfile(cfg.cwd)
    stdout_console.print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise typer.Exit(1)


@marketplace_app.command("install")
def marketplace_install(
    target: str = typer.Argument(..., help="name@version or path/to/bundle.askill"),
    scope: str = typer.Option("user", "--scope", help="user|project"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Install a signed skill bundle."""
    from agent.events import EventEmitter
    from agent.skills.marketplace import install_bundle, marketplace_dir

    cfg = Config.resolve(cwd=cwd)
    skills_cfg = load_skills_config(cfg.config_path)
    mp = skills_cfg.marketplace
    bundle_path: Path
    if "@" in target and not target.endswith(".askill"):
        name, version = target.split("@", 1)
        bundle_path = marketplace_dir(mp.registry_dir) / "cache" / f"{name}-{version}.tar.gz"
    else:
        bundle_path = Path(target)
    emitter = EventEmitter(lambda e: None)
    result = install_bundle(
        bundle_path,
        settings=mp,
        scope=scope,
        cwd=cfg.cwd,
        emitter=emitter,
    )
    if not result.get("ok"):
        console.print(f"[red]Install failed:[/red] {result.get('error')}")
        raise typer.Exit(1)
    console.print(f"[green]Installed[/green] {result['name']}@{result.get('version')} → {result['path']}")


@marketplace_app.command("verify")
def marketplace_verify(
    name: str = typer.Argument(...),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Verify installed or cached marketplace bundle."""
    from agent.skills.marketplace import marketplace_dir, verify_bundle

    cfg = Config.resolve(cwd=cwd)
    mp = load_skills_config(cfg.config_path).marketplace
    cache = marketplace_dir(mp.registry_dir) / "cache"
    matches = list(cache.glob(f"{name}-*.tar.gz"))
    if not matches:
        console.print(f"[red]No cached bundle for[/red] {name}")
        raise typer.Exit(1)
    result = verify_bundle(matches[-1], settings=mp)
    if result.ok:
        console.print(f"[green]Valid[/green] {name} ({result.reason or 'ok'})")
    else:
        console.print(f"[red]Invalid:[/red] {result.reason}")
        raise typer.Exit(1)


@marketplace_app.command("uninstall")
def marketplace_uninstall(
    name: str = typer.Argument(...),
    scope: str = typer.Option("user", "--scope"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    from agent.skills.marketplace import uninstall_skill

    cfg = Config.resolve(cwd=cwd)
    result = uninstall_skill(name, scope=scope, cwd=cfg.cwd)
    if not result.get("ok"):
        console.print(f"[red]{result.get('error')}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Uninstalled[/green] {name}")


@marketplace_app.command("trust-key")
def marketplace_trust_key(
    publisher: str = typer.Argument(...),
    key_path: Path = typer.Argument(..., help="Path to .ed25519.pub"),
) -> None:
    """Add a trusted publisher public key."""
    from agent.skills.marketplace import marketplace_dir

    cfg = Config.resolve()
    mp = load_skills_config(cfg.config_path).marketplace
    dest = marketplace_dir(mp.registry_dir) / "keys" / f"{publisher}.ed25519.pub"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(key_path.read_bytes())
    console.print(f"[green]Trusted key installed:[/green] {dest}")


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


@threads_app.command("pick")
def threads_pick(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Filter threads to this cwd"),
    all_cwds: bool = typer.Option(False, "--all", help="Show threads from all cwds"),
) -> None:
    """Interactively pick a thread from saved history."""
    from agent.threads_picker import format_thread_picker_table, list_threads_for_picker, pick_thread

    store = ThreadStore()
    target = (cwd or Path.cwd()).resolve()
    threads = list_threads_for_picker(store, target, filter_cwd=not all_cwds)
    if not threads:
        console.print("No threads found.")
        raise typer.Exit(1)

    table = Table(title="Pick a thread")
    table.add_column("#", style="cyan")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Model")
    table.add_column("Updated")
    table.add_column("Cost")
    for row in format_thread_picker_table(threads):
        table.add_row(
            str(row["index"]),
            row["id"],
            row["title"],
            row["model"],
            row["updated"],
            row["cost"],
        )
    console.print(table)
    picked = pick_thread(store, target, filter_cwd=not all_cwds)
    if not picked:
        raise typer.Exit(1)
    console.print(f"Selected: {picked.id}")


@threads_app.command("fork")
def threads_fork(
    thread_id: Optional[str] = typer.Argument(None, help="Source thread ID (full or prefix)"),
    title: Optional[str] = typer.Option(None, "--title", help="Title for forked thread"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Cwd filter when picking interactively"),
) -> None:
    """Fork a thread (branch conversation history)."""
    from agent.threads_picker import pick_thread

    store = ThreadStore()
    source: Thread | None = None
    if thread_id:
        source = _load_thread(store, thread_id)
    else:
        source = pick_thread(store, (cwd or Path.cwd()).resolve())
        if not source:
            console.print("[red]Error:[/red] No thread selected")
            raise typer.Exit(1)
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


@exec_policy_app.command("amend")
def exec_policy_amend(
    prefix: str = typer.Option(..., "--prefix", help="Command prefix to allow for this project"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
) -> None:
    """Append a command prefix to the project exec-policy allow list."""
    from agent.exec_policy import append_allow_prefix, load_allow_prefixes

    root = (cwd or Path.cwd()).resolve()
    append_allow_prefix(root, prefix)
    prefixes = load_allow_prefixes(root)
    stdout_console.print({"cwd": str(root), "prefixes": prefixes})


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


@sync_app.command("fetch-remote")
def sync_fetch_remote_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
) -> None:
    """Fetch remote sync manifest over SSH and optionally replicate sync-state."""
    import json

    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.backend = "ssh"
    config.execution.ssh.sync_enabled = True
    stdout_console.print(
        json.dumps(run_sync_fetch_remote(config, thread_id=thread_id), indent=2)
    )


@sync_app.command("plan")
def sync_plan_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    ssh_host: Optional[str] = typer.Option(None, "--ssh-host"),
    ssh_user: Optional[str] = typer.Option(None, "--ssh-user"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    verbose: bool = typer.Option(False, "--verbose", help="Print push/pull/conflict table"),
) -> None:
    """Dry-run sync plan: push/pull/conflict classification."""
    import json

    config = _resolve_ssh_config(cwd, ssh_host, ssh_user)
    config.execution.backend = "ssh"
    config.execution.ssh.sync_enabled = True
    plan = run_sync_plan(config, thread_id=thread_id)
    if verbose:
        stdout_console.print(format_plan_verbose(plan))
    else:
        stdout_console.print(json.dumps(plan, indent=2))


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


@schedule_notifications_app.command("test")
def schedule_notifications_test(
    event: str = typer.Option("failed", "--event"),
    job_id: str = typer.Option("nightly-tests", "--job-id"),
) -> None:
    """Send a test schedule notification payload."""
    import json

    from agent.schedule.notifications import notify_job_result
    from agent.settings import load_schedule_settings

    sched = load_schedule_settings()
    sample = {
        "ok": event != "failed",
        "status": "failed" if event == "failed" else "completed",
        "error": "simulated failure" if event == "failed" else None,
        "thread_id": "thread-test",
        "turn_id": "turn-test",
        "snippet": "pytest failed: 1 error",
    }
    if event == "budget_exceeded":
        sample = {
            "ok": False,
            "error": "budget exceeded",
            "budget": {"metric": "max_workers_spawned", "limit": 20, "observed": 21},
        }
    result = notify_job_result(job_id, sample, sched.notifications)
    stdout_console.print(json.dumps(result, indent=2))


@auth_webhooks_app.command("test")
def auth_webhooks_test(
    event: str = typer.Option("role_changed", "--event"),
    subject: str = typer.Option("user-123", "--subject"),
) -> None:
    """Simulate OIDC webhook revocation locally."""
    import json
    import os

    from agent.auth.policy.sessions import SessionRevocationRegistry
    from agent.auth.webhooks.oidc_events import parse_oidc_event
    from agent.auth.webhooks.revoke import revoke_for_event
    from agent.events import EventEmitter
    from agent.settings import load_serve_settings
    from agent.serve.sessions import SessionStore

    cfg = load_serve_settings()
    wh = cfg.webhooks
    payload = parse_oidc_event({"event": event, "subject": subject})
    SessionStore.reset_for_tests()
    store = SessionStore.global_store()
    store.create_session(
        __import__("agent.serve.rbac", fromlist=["AuthPrincipal"]).AuthPrincipal(
            name="u", role="operator", auth_method="oidc"
        ),
        subject=subject,
    )
    result = revoke_for_event(
        payload,
        session_store=store,
        registry=SessionRevocationRegistry(),
        revoke_on_events=wh.revoke_on_events or [event],
        emitter=EventEmitter(),
    )
    stdout_console.print(json.dumps(result, indent=2))


@serve_webhooks_app.command("status")
def serve_webhooks_status() -> None:
    import json
    import os

    from agent.settings import load_serve_settings

    wh = load_serve_settings().webhooks
    stdout_console.print(
        json.dumps(
            {
                "enabled": wh.enabled,
                "path": wh.path,
                "secret_configured": bool(os.environ.get(wh.shared_secret_env)),
                "revoke_on_events": wh.revoke_on_events,
            },
            indent=2,
        )
    )


@programs_sync_app.command("push")
def programs_sync_push(
    program_id: str | None = typer.Option(None, "--program-id"),
) -> None:
    import json

    from agent.programs.sync.coordinator import ProgramSyncCoordinator

    cfg = Config.resolve()
    coord = ProgramSyncCoordinator(cfg)
    if program_id:
        stdout_console.print(json.dumps(coord.push(program_id), indent=2))
        return
    results = [coord.push(pid) for pid in coord.store.list_programs()]
    stdout_console.print(json.dumps(results, indent=2))


@programs_sync_app.command("pull")
def programs_sync_pull(
    program_id: str | None = typer.Option(None, "--program-id"),
) -> None:
    import json

    from agent.programs.sync.coordinator import ProgramSyncCoordinator

    cfg = Config.resolve()
    coord = ProgramSyncCoordinator(cfg)
    if program_id:
        stdout_console.print(json.dumps(coord.pull(program_id), indent=2))
        return
    results = [coord.pull(pid) for pid in coord.store.list_programs()]
    stdout_console.print(json.dumps(results, indent=2))


@programs_sync_app.command("status")
def programs_sync_status() -> None:
    import json
    from dataclasses import asdict

    from agent.programs.sync.coordinator import ProgramSyncCoordinator

    cfg = Config.resolve()
    coord = ProgramSyncCoordinator(cfg)
    stdout_console.print(json.dumps(asdict(coord.status()), indent=2))


@programs_sync_app.command("verify-signature")
def programs_sync_verify_signature(program_id: str = typer.Argument(...)) -> None:
    import json

    from agent.programs.sync.coordinator import ProgramSyncCoordinator

    cfg = Config.resolve()
    coord = ProgramSyncCoordinator(cfg)
    stdout_console.print(json.dumps(coord.verify_signature(program_id), indent=2))


@programs_app.command("list")
def programs_list() -> None:
    """List cross-thread program DAG ids."""
    import json

    from agent.multi_agent.program_state import ProgramStore

    cfg = Config.resolve()
    store = ProgramStore(Path(cfg.multi_agent.cross_thread.state_dir).expanduser())
    stdout_console.print(json.dumps({"programs": store.list_programs()}, indent=2))


@programs_app.command("show")
def programs_show(program_id: str = typer.Argument(...)) -> None:
    """Show program-wide DAG state."""
    import json

    from agent.multi_agent.program_state import ProgramStore

    cfg = Config.resolve()
    store = ProgramStore(Path(cfg.multi_agent.cross_thread.state_dir).expanduser())
    state = store.load(program_id)
    if not state:
        stdout_console.print(json.dumps({"error": "not found"}, indent=2))
        raise typer.Exit(1)
    stdout_console.print(json.dumps(state.to_dict(), indent=2))


@programs_app.command("link-thread")
def programs_link_thread(
    program_id: str = typer.Option(..., "--program-id"),
    thread_id: str = typer.Option(..., "--thread-id"),
) -> None:
    """Link a thread to a program DAG."""
    import json

    from agent.multi_agent.program_state import ProgramStore

    cfg = Config.resolve()
    ct = cfg.multi_agent.cross_thread
    store = ProgramStore(Path(ct.state_dir).expanduser())
    try:
        state = store.link_thread(program_id, thread_id, max_threads=ct.max_threads_linked)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    stdout_console.print(json.dumps(state.to_dict(), indent=2))


@programs_app.command("clear")
def programs_clear(
    program_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Clear a program DAG state file."""
    if not yes and not typer.confirm(f"Clear program {program_id}?"):
        raise typer.Exit(0)
    from agent.multi_agent.program_state import ProgramStore

    cfg = Config.resolve()
    cleared = ProgramStore(Path(cfg.multi_agent.cross_thread.state_dir).expanduser()).clear(program_id)
    if cleared:
        console.print(f"[green]Cleared[/green] program {program_id}")
    else:
        console.print("[yellow]Program not found[/yellow]")


@multi_agent_budgets_app.command("show")
def multi_agent_budgets_show(
    thread_id: str = typer.Option(..., "--thread-id"),
) -> None:
    """Show persisted swarm budget snapshot for a thread."""
    import json

    from agent.multi_agent.budget_state import load_budget_state

    snapshot = load_budget_state(thread_id)
    if not snapshot:
        stdout_console.print(json.dumps({"thread_id": thread_id, "found": False}, indent=2))
        raise typer.Exit(1)
    stdout_console.print(json.dumps({"thread_id": thread_id, "snapshot": snapshot}, indent=2))


@multi_agent_app.command("status")
def multi_agent_status(
    thread_id: str = typer.Option(..., "--thread-id"),
    verbose: bool = typer.Option(False, "--verbose", help="Show attempts, backoff, errors"),
) -> None:
    """Show multi-agent checkpoint status for a thread."""
    import json

    rows = list_checkpoint_status(thread_id, verbose=verbose)
    stdout_console.print(json.dumps(rows, indent=2))


@multi_agent_app.command("graph")
def multi_agent_graph(
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    turn_id: Optional[str] = typer.Option(None, "--turn-id"),
    program_id: Optional[str] = typer.Option(None, "--program-id"),
) -> None:
    """Show worker DAG nodes and edges from checkpoint or program scope."""
    import json

    if program_id:
        from agent.multi_agent.program_state import ProgramStore

        cfg = Config.resolve()
        store = ProgramStore(Path(cfg.multi_agent.cross_thread.state_dir).expanduser())
        stdout_console.print(json.dumps(store.graph_snapshot(program_id), indent=2))
        return
    if not thread_id:
        console.print("[red]--thread-id or --program-id required[/red]")
        raise typer.Exit(1)
    from agent.multi_agent.checkpoint import CheckpointStore

    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    config = Config.resolve(cwd=Path(thread.cwd))
    cp_store = CheckpointStore(Path(config.multi_agent.checkpoint_dir).expanduser())
    cp = cp_store.load(thread.id, turn_id) if turn_id else cp_store.find_latest(thread.id)
    if not cp:
        stdout_console.print(json.dumps({"nodes": [], "edges": [], "status": "idle"}, indent=2))
        return
    stdout_console.print(
        json.dumps(
            {
                "nodes": [
                    {
                        "worker_id": w.worker_id,
                        "status": w.status,
                        "attempts": w.attempts,
                        "worker_dependencies": w.worker_dependencies,
                        "task": w.task,
                        "error": w.error,
                    }
                    for w in cp.workers
                ],
                "edges": cp.edges,
                "status": cp.dag_status,
                "turn_id": cp.turn_id,
            },
            indent=2,
        )
    )


@multi_agent_app.command("dag-status")
def multi_agent_dag_status(
    thread_id: str = typer.Option(..., "--thread-id"),
) -> None:
    """Show cross-turn persisted DAG state for a thread."""
    import json

    from agent.multi_agent.dag_state import DagStateStore

    state = DagStateStore().load(thread_id)
    if not state:
        stdout_console.print(json.dumps({"status": "empty"}, indent=2))
        return
    stdout_console.print(json.dumps(state.to_dict(), indent=2))


@multi_agent_app.command("dag-clear")
def multi_agent_dag_clear(
    thread_id: str = typer.Option(..., "--thread-id"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Clear persisted cross-turn DAG state."""
    if not yes:
        if not typer.confirm(f"Clear DAG state for {thread_id}?"):
            raise typer.Exit(0)
    from agent.multi_agent.dag_state import DagStateStore

    cleared = DagStateStore().clear(thread_id)
    if cleared:
        console.print(f"[green]Cleared[/green] DAG state for {thread_id}")
    else:
        console.print("[dim]No DAG state file found[/dim]")


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
    emitter = build_event_emitter(
        recording=config.recording.enabled,
        action_log=config.action_log,
        project_cwd=config.cwd,
        model=config.model,
    )
    resume_supervisor_turn(
        thread, cp, config, store, retry_failed=retry_failed, events=emitter
    )
    console.print(f"[green]Resumed[/green] turn {cp.turn_id} on thread {thread.id}")


@threads_app.command("show")
def threads_show(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
    usage: bool = typer.Option(False, "--usage", help="Show per-turn token/cost usage"),
    stats: bool = typer.Option(False, "--stats", help="Show per-turn file/command stats"),
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
        if usage and turn.usage:
            u = turn.usage
            cost = f", ${u.estimated_cost_usd:.4f}" if u.estimated_cost_usd else ""
            model_used = f", model={u.model_used}" if u.model_used else ""
            fb = ", fallback" if u.fallback_used else ""
            console.print(
                f"[dim]Usage: in={u.input_tokens} out={u.output_tokens}{cost}{model_used}{fb}[/dim]"
            )
        if stats:
            from agent.turn_stats import aggregate_turn_stats, format_turn_stats_brief

            console.print(f"[dim]Stats: {format_turn_stats_brief(aggregate_turn_stats(turn))}[/dim]")
        console.print()


@threads_app.command("cost")
def threads_cost(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
) -> None:
    """Show accumulated token and cost estimate for a thread."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    import json

    stdout_console.print(json.dumps(thread_cost_summary(thread), indent=2))


@threads_app.command("pr-description")
def threads_pr_description(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
    title: Optional[str] = typer.Option(None, "--title", help="PR title override"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write markdown to file"),
    no_diff: bool = typer.Option(False, "--no-diff", help="Omit git diff stat section"),
    summary_only: bool = typer.Option(
        False, "--summary-only", help="Summary bullets only (no conversation log)"
    ),
) -> None:
    """Generate a PR description from thread transcript and git diff stat."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    md = build_pr_description(
        thread,
        title=title,
        include_diff=not no_diff,
        include_conversation=not summary_only,
    )
    if out:
        out.write_text(md, encoding="utf-8")
        console.print(f"Wrote PR description to {out}")
    else:
        stdout_console.print(md, markup=False, highlight=False)


@threads_app.command("checkpoint-status")
def threads_checkpoint_status(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
) -> None:
    """Show single-agent turn checkpoints for a thread."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    config = Config.resolve(cwd=Path(thread.cwd))
    cp_store = TurnCheckpointStore(Path(config.turn_checkpoint.dir).expanduser())
    checkpoints = cp_store.list_for_thread(thread.id)
    if not checkpoints:
        console.print("No turn checkpoints saved.")
        return
    table = Table(title=f"Turn checkpoints — {thread.id[:8]}…")
    table.add_column("Turn ID")
    table.add_column("Status")
    table.add_column("Saved")
    table.add_column("Prompt")
    for cp in checkpoints:
        table.add_row(
            cp.turn_id[:12] + "…",
            cp.status,
            cp.saved_at[:19],
            (cp.user_text[:50] + "…") if len(cp.user_text) > 50 else cp.user_text,
        )
    console.print(table)


@threads_app.command("resume-turn")
def threads_resume_turn(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
    auto_approve: bool = typer.Option(False, "--auto-approve"),
) -> None:
    """Resume the latest cancelled single-agent turn from checkpoint."""
    store = ThreadStore()
    thread = _load_thread(store, thread_id)
    config = Config.resolve(cwd=Path(thread.cwd))
    config.require_api_key()
    cp_store = TurnCheckpointStore(Path(config.turn_checkpoint.dir).expanduser())
    resume_cp = cp_store.find_latest(thread.id)
    if not resume_cp:
        console.print("[red]Error:[/red] No turn checkpoint found.")
        raise typer.Exit(1)
    output = OutputHandler()
    emitter = build_event_emitter(
        output.handle,
        recording=config.recording.enabled,
        action_log=config.action_log,
        project_cwd=config.cwd,
        model=config.model,
    )
    console.print(f"[dim]Resuming turn {resume_cp.turn_id[:8]}…[/dim]")
    turn = run_turn(
        thread,
        resume_cp.user_text,
        config,
        store,
        events=emitter,
        session_auto_approve=auto_approve or config.auto_approve,
        resume_checkpoint=resume_cp,
    )
    console.print(format_run_summary(turn), markup=False)


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


@runs_app.command("bundle-info")
def runs_bundle_info(
    bundle: Path = typer.Argument(..., help="Path to run.bundle.tar.gz"),
) -> None:
    """Print manifest from a run bundle without extracting."""
    from agent.recording.bundle import read_bundle_manifest

    try:
        manifest = read_bundle_manifest(bundle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    sys.stdout.write(json.dumps(manifest, indent=2) + "\n")


@runs_app.command("export")
def runs_export(
    turn_id: str = typer.Argument(..., help="Turn ID (full or prefix)"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output markdown file"),
    format: str = typer.Option("markdown", "--format", help="markdown | jsonl-v2 | bundle"),
) -> None:
    """Export run log to Markdown or normalized JSONL."""
    store = RunStore()
    try:
        events = store.load_events(turn_id, thread_id=thread_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if format == "jsonl-v2":
        from agent.json_stream import normalize_event

        lines = [normalize_event(ev).to_json() for ev in events]
        content = "\n".join(lines)
        if out:
            out.write_text(content, encoding="utf-8")
            console.print(f"Exported to {out}")
        else:
            stdout_console.print(content)
        return
    if format == "bundle":
        if not out:
            console.print("[red]Error:[/red] --out is required for bundle export")
            raise typer.Exit(1)
        from agent.recording.bundle import export_run_bundle

        cfg = Config.resolve()
        manifest = export_run_bundle(
            turn_id=turn_id,
            thread_id=thread_id,
            out_path=out,
            config=cfg,
            run_store=store,
        )
        console.print(f"Exported bundle to {out} (thread={manifest['thread_id'][:8]}…)")
        return
    md = export_run_markdown(events)
    if out:
        out.write_text(md, encoding="utf-8")
        console.print(f"Exported to {out}")
    else:
        stdout_console.print(md)


@serve_app.callback()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", help="Bind port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth bearer token"),
    no_control: bool = typer.Option(False, "--no-control", help="Disable cancel API"),
    enable_turn_start: bool = typer.Option(
        False, "--enable-turn-start", help="Allow HTTP POST /threads/{id}/run"
    ),
    max_concurrent_turns: int = typer.Option(2, "--max-concurrent-turns"),
    allow_remote_bind: bool = typer.Option(
        False, "--allow-remote-bind", help="Allow binding to 0.0.0.0"
    ),
    tls: bool = typer.Option(False, "--tls", help="Enable TLS (requires cert/key in config)"),
    generate_self_signed: bool = typer.Option(
        False, "--generate-self-signed", help="Generate dev self-signed cert before start"
    ),
    enable_ide: bool = typer.Option(
        False, "--enable-ide", help="Enable web IDE lite (file tree + Monaco editor)"
    ),
) -> None:
    """HTTP dashboard for threads, runs, SSE events, turn cancel, and optional turn start."""
    if ctx.invoked_subcommand is not None:
        return
    from agent.serve.server import serve as run_serve

    cfg = load_serve_settings()
    cfg.host = host
    cfg.port = port
    cfg.enable_control = not no_control
    cfg.enable_turn_start = enable_turn_start
    cfg.max_concurrent_turns = max_concurrent_turns
    cfg.allow_remote_bind = allow_remote_bind
    if token:
        cfg.auth_token = token
    if tls or generate_self_signed:
        cfg.tls.enabled = True
        cfg.tls.auto_generate_self_signed = generate_self_signed
    if enable_ide:
        cfg.ide.enabled = True
    if cfg.oidc and getattr(cfg.oidc, "refresh_rotation", True):
        from agent.auth.oidc_tokens import OidcTokenStore
        from agent.auth.refresh import ensure_fresh_tokens

        storage = load_auth_storage_settings(cfg.config_path if hasattr(cfg, "config_path") else None)
        try:
            ensure_fresh_tokens(OidcTokenStore(storage_settings=storage), cfg.oidc)
        except Exception:
            pass
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
        run_serve(
            host=host,
            port=port,
            settings=cfg,
            auth_token=token or cfg.auth_token or None,
            generate_self_signed=generate_self_signed,
        )
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@telemetry_app.command("status")
def telemetry_status_cmd(
    verbose: bool = typer.Option(False, "--verbose", help="Include histogram buckets and span stats"),
) -> None:
    """Show telemetry configuration and exporter availability."""
    import json

    cfg = Config.resolve()
    provider = __import__("agent.telemetry", fromlist=["TracerProvider"]).TracerProvider.global_provider()
    provider.configure(cfg.telemetry)
    otel_installed = False
    try:
        import opentelemetry  # noqa: F401

        otel_installed = True
    except ImportError:
        pass
    payload = {
        "enabled": cfg.telemetry.enabled,
        "service_name": cfg.telemetry.service_name,
        "otlp_endpoint": cfg.telemetry.otlp_endpoint,
        "sample_rate": cfg.telemetry.sample_rate,
        "export_console": cfg.telemetry.export_console,
        "export_runtime_metrics": cfg.telemetry.export_runtime_metrics,
        "otel_packages_installed": otel_installed,
        "otel_runtime_available": provider._otel_available,
    }
    if verbose:
        payload["histogram_buckets_sec"] = cfg.telemetry.histogram_buckets_sec
        payload["memory_spans"] = len(provider._memory.spans)
        payload["trace_context_sample"] = provider.trace_context()
    stdout_console.print(json.dumps(payload, indent=2))


@metrics_app.command("show")
def metrics_show(
    format: str = typer.Option("json", "--format", help="json or prometheus"),
) -> None:
    """Print runtime metrics (JSON or Prometheus text)."""
    collector = MetricsCollector.global_collector()
    if format.lower() == "prometheus":
        stdout_console.print(collector.to_prometheus(), end="")
    else:
        stdout_console.print(collector.to_json())


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


@app.command("mcp-server")
def mcp_server_cmd() -> None:
    """Run stdio MCP server exposing agent_run for external clients."""
    from agent.mcp_server.server import run_stdio_server

    run_stdio_server()


@app.command("lsp-mcp")
def lsp_mcp_cmd(
    workspace: str = typer.Option(
        ".",
        "--workspace",
        "-w",
        help="Workspace root passed to language servers",
    ),
) -> None:
    """Run stdio MCP server exposing LSP navigation tools (Pyright, typescript-language-server)."""
    import os

    os.environ["WORKSPACE_ROOT"] = str(Path(workspace).resolve())
    from agent.lsp_mcp.server import run_stdio_server

    run_stdio_server()


@app.command("tui")
def tui_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory"),
    thread_id: Optional[str] = typer.Option(None, "--thread-id", help="Resume thread"),
    resume_last: bool = typer.Option(False, "--resume-last", help="Resume latest thread"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Named run profile"),
    model_profile: Optional[str] = typer.Option(None, "--model-profile", help="Model profile"),
) -> None:
    """Interactive terminal UI for agent sessions."""
    try:
        resolved_config = Config.resolve(
            cwd=cwd,
            profile=profile,
            model_profile=model_profile,
        )
        resolved_config.require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    try:
        launch_interactive_session(
            cwd=cwd,
            thread_id=thread_id,
            resume_last=resume_last,
            profile=profile,
            model_profile=model_profile,
            config=resolved_config,
        )
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


def _git_commit_short() -> str | None:
    import subprocess

    try:
        root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@app.command("init")
def init_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Git repo root (default: cwd)"),
    yes: bool = typer.Option(False, "--yes", help="Overwrite existing scaffold files"),
    name: Optional[str] = typer.Option(None, "--name", help="Project name in config"),
    skip_git_check: bool = typer.Option(
        False,
        "--skip-git-check",
        help="Allow init outside a git repository",
    ),
) -> None:
    """Scaffold .agent-cli/ config, AGENTS.md, and seed skill in the current git repo."""
    root = (cwd or Path.cwd()).resolve()
    if detect_repo_root(root) is None:
        if skip_git_check:
            console.print(
                "[yellow]Warning:[/yellow] Not inside a git repository. "
                "Run `git init` when ready."
            )
        else:
            console.print(
                "[yellow]Warning:[/yellow] Not inside a git repository. "
                "Run `git init` or pass --skip-git-check."
            )
            raise typer.Exit(1)
    created = init_project(root, name=name, yes=yes)
    created_files = {k: v for k, v in created.items() if not k.startswith("skipped") and k != "message"}
    skipped = {k: v for k, v in created.items() if k.startswith("skipped") or k == "message"}
    for kind, path in created_files.items():
        console.print(f"[green]Created[/green] {kind}: {path}")
    for kind, msg in skipped.items():
        console.print(f"[dim]{msg}[/dim]")
    if not created_files and not skipped:
        console.print("[yellow]Nothing to create[/yellow]")
    console.print("Next: set OPENROUTER_API_KEY and run `agent run \"your task\"`")


@app.command("apply")
def apply_cmd(
    thread_id: Optional[str] = typer.Option(None, "--thread-id", help="Thread to read patches from"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory for apply"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview patches without applying"),
    last: bool = typer.Option(True, "--last/--turn", help="Use last completed patch turn"),
) -> None:
    """Apply the last agent patch from a saved thread."""
    from agent.apply_last import apply_last_for_thread_id

    store = ThreadStore()
    if not thread_id:
        thread = store.find_latest_for_cwd(str((cwd or Path.cwd()).resolve()))
        if not thread:
            console.print("[red]Error:[/red] No thread found for cwd")
            raise typer.Exit(1)
        thread_id = thread.id
    if not last:
        console.print("[yellow]Only --last is supported in v2.6.0[/yellow]")
    result = apply_last_for_thread_id(store, thread_id, cwd=cwd, dry_run=dry_run)
    if result.error:
        console.print(f"[red]Error:[/red] {result.error}")
        raise typer.Exit(1)
    for block in result.preview_lines:
        stdout_console.print(block)
    if dry_run:
        console.print("[dim]Dry run — no files changed[/dim]")
        return
    if result.outcome:
        for patch_result in result.outcome.results:
            console.print(f"[green]Applied[/green] {patch_result.path} ({patch_result.change_type})")


@app.command("repl")
def repl_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    model_profile: Optional[str] = typer.Option(None, "--model-profile"),
    resume_last: bool = typer.Option(False, "--resume-last"),
    resume: bool = typer.Option(False, "--resume", help="Pick a thread to resume"),
    ephemeral: bool = typer.Option(False, "--ephemeral", help="Do not persist threads"),
    resume_turn: bool = typer.Option(
        False, "--resume-turn", help="Resume cancelled turn from checkpoint on next prompt"
    ),
) -> None:
    """Interactive multi-turn REPL (no Textual required)."""
    from agent.init_scaffold import ensure_workspace_ready

    try:
        Config.resolve(
            cwd=cwd,
            profile=profile,
            model_profile=model_profile,
        ).require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    root = (cwd or Path.cwd()).resolve()
    ensure_workspace_ready(root)
    run_repl(
        cwd=cwd,
        profile=profile,
        model_profile=model_profile,
        resume_last=resume_last,
        resume=resume,
        ephemeral=ephemeral,
        resume_turn=resume_turn,
    )


@profile_app.command("list")
def profile_list(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """List named run profiles from config layers."""
    root = (cwd or Path.cwd()).resolve()
    user = default_config_path()
    project = project_config_path(root)
    profiles = {**load_run_profiles(user), **load_run_profiles(project)}
    if not profiles:
        console.print("No [profiles.*] defined. Default profile keys: interactive, ci")
        return
    table = Table(title="Run profiles")
    table.add_column("Name")
    table.add_column("Model profile")
    table.add_column("Sandbox")
    table.add_column("Approval")
    for name, rp in profiles.items():
        table.add_row(name, rp.model_profile or "-", rp.sandbox_mode or "-", rp.approval_mode or "-")
    console.print(table)


@profile_app.command("show")
def profile_show(
    name: str = typer.Argument(..., help="Profile name"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Show merged effective settings for a profile."""
    root = (cwd or Path.cwd()).resolve()
    merged = merge_layered_config(root, cli_profile=name)
    import json

    stdout_console.print(json.dumps({k: v for k, v in merged.items() if not k.startswith("_")}, indent=2))


@profile_app.command("use")
def profile_use(
    name: str = typer.Argument(..., help="Profile name to write as default"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Set default profile in project .agent-cli/config.toml."""
    root = (cwd or Path.cwd()).resolve()
    path = project_config_path(root)
    if not path.parent.is_dir():
        console.print("[red]Run agent init first.[/red]")
        raise typer.Exit(1)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "profile =" in text:
        lines = []
        for line in text.splitlines():
            if line.strip().startswith("profile ="):
                lines.append(f'profile = "{name}"')
            else:
                lines.append(line)
        text = "\n".join(lines) + "\n"
    else:
        text = f'profile = "{name}"\n' + text
    path.write_text(text, encoding="utf-8")
    console.print(f"[green]Set profile[/green] to {name} in {path}")


@models_app.command("list")
def models_list(
    refresh: bool = typer.Option(False, "--refresh", help="Fetch from OpenRouter API"),
) -> None:
    """List cached OpenRouter models."""
    cache = ModelsCache()
    models = cache.load()
    if refresh or not models:
        try:
            cfg = Config.resolve()
            cfg.require_api_key()
            models = cache.fetch(cfg.require_api_key())
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if not models:
        console.print("No models cached. Set OPENROUTER_API_KEY and retry with --refresh")
        raise typer.Exit(1)
    table = Table(title=f"OpenRouter models ({len(models)})")
    table.add_column("ID")
    table.add_column("Name")
    for m in models[:50]:
        table.add_row(str(m.get("id", "")), str(m.get("name", ""))[:60])
    console.print(table)
    if len(models) > 50:
        console.print(f"[dim]… and {len(models) - 50} more[/dim]")


@models_app.command("recommend")
def models_recommend(
    task: str = typer.Option(..., "--task", help="Task description for model pick"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Recommend a model for a task from cached listing."""
    root = (cwd or Path.cwd()).resolve()
    cache = ModelsCache()
    models = cache.load()
    if not models:
        try:
            cfg = Config.resolve(cwd=root)
            models = cache.fetch(cfg.require_api_key())
        except ValueError:
            models = []
    pick = recommend_model(task, models, cwd=root)
    console.print(f"Recommended: [green]{pick}[/green]")


@tools_app.command("list")
def tools_list_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
    include_mcp: bool = typer.Option(True, "--mcp/--no-mcp", help="Probe MCP tools"),
) -> None:
    """List built-in and MCP tools with descriptions."""
    cfg = Config.resolve(cwd=cwd)
    mcp_manager = None
    if include_mcp:
        mcp_cfg = load_mcp_config(cfg.config_path, cfg.cwd)
        mcp_manager = McpManager(mcp_cfg)
        try:
            mcp_manager.connect_all(
                on_failed=lambda s, e: console.print(f"[dim]MCP {s}: {e}[/dim]"),
            )
        except Exception:
            pass
    schemas = get_tool_schemas(mcp_manager, cfg, allow_spawn=cfg.multi_agent.enabled)
    table = Table(title="Tools")
    table.add_column("Name")
    table.add_column("Description")
    for sch in schemas:
        fn = sch.get("function", {})
        table.add_row(str(fn.get("name", "")), str(fn.get("description", ""))[:80])
    console.print(table)
    if mcp_manager:
        mcp_manager.disconnect_all()


@skills_app.command("doctor")
def skills_doctor_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
) -> None:
    """Check skills for missing frontmatter, duplicates, oversized bodies."""
    root = (cwd or Path.cwd()).resolve()
    report = skills_doctor_report(root)
    if report["ok"] and not report["issues"]:
        console.print("[green]All skills OK[/green]")
        return
    for issue in report["issues"]:
        color = "red" if issue["level"] == "error" else "yellow"
        console.print(f"[{color}]{issue['level']}[/] {issue['skill']}: {issue['message']}")
    if not report["ok"]:
        raise typer.Exit(1)


@app.command("context")
def context_cmd(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Working directory"),
    thread_id: Optional[str] = typer.Option(None, "--thread", help="Thread ID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable breakdown"),
) -> None:
    """Show context window usage breakdown for a thread."""
    import json as json_mod

    from agent.context_meter import (
        build_context_snapshot,
        format_context_breakdown,
        snapshot_to_dict,
    )
    from agent.store import ThreadStore

    config = Config.resolve(cwd=cwd)
    store = ThreadStore(config.cwd)
    thread = None
    if thread_id:
        thread = store.load_thread(thread_id)
        if thread is None:
            typer.echo(f"Thread not found: {thread_id}", err=True)
            raise typer.Exit(1)
    else:
        threads = store.list_threads()
        if threads:
            thread = store.load_thread(threads[0].id)

    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    if json_output:
        stdout_console.print(json_mod.dumps(snapshot_to_dict(snap), indent=2))
    else:
        stdout_console.print(format_context_breakdown(snap))


@app.command("version")
def version_cmd() -> None:
    """Print semver and git commit (if available)."""
    from importlib.metadata import version as pkg_version

    ver = "unknown"
    try:
        ver = pkg_version("agent-cli")
    except Exception:
        pass
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version ="):
                ver = line.split("=", 1)[1].strip().strip('"')
                break
    commit = _git_commit_short()
    if commit:
        stdout_console.print(f"agent-cli {ver} (commit {commit})")
    else:
        stdout_console.print(f"agent-cli {ver}")


def main() -> None:
    from agent.env_loader import load_dotenv_files

    load_dotenv_files()
    app()


if __name__ == "__main__":
    main()
