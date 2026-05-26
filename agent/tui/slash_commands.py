from __future__ import annotations

from dataclasses import dataclass

from agent.compaction import force_compact_thread
from agent.config import Config
from agent.models import Thread
from agent.plan_mode import plan_summary
from agent.profiles import thread_cost_summary
from agent.store import ThreadStore
from model.openrouter import OpenRouterClient


@dataclass
class TuiSlashState:
    model_override: str | None = None
    profile_override: str | None = None
    model_profile_override: str | None = None
    plan_mode: bool = False
    last_routing_prompt: str = ""


@dataclass
class SlashCommandResult:
    handled: bool
    message: str = ""
    quit_app: bool = False
    clear_session: bool = False
    enter_chat: bool = False
    reload_thread: bool = False


def parse_slash_command(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return name, arg


def execute_slash_command(
    name: str,
    arg: str,
    *,
    state: TuiSlashState,
    config: Config,
    store: ThreadStore,
    thread: Thread | None,
) -> SlashCommandResult:
    if name in ("/quit", "/exit", "/q"):
        return SlashCommandResult(handled=True, quit_app=True, message="Goodbye.")

    if name == "/help":
        return SlashCommandResult(
            handled=True,
            message=(
                "Commands: /model [id], /model-profile [name], /why-model, /plan [on|off], "
                "/context [--json], /compact, /clear, /cost, /thread, /memories, /statusline, /quit"
            ),
        )

    if name == "/memories":
        return _handle_memories(arg, config=config, thread=thread)

    if name == "/statusline":
        from agent.tui.statusline import DEFAULT_STATUSLINE, load_statusline_settings

        settings = load_statusline_settings(config.config_path)
        if arg.lower() == "default":
            return SlashCommandResult(
                handled=True,
                message=f"Default items: {', '.join(DEFAULT_STATUSLINE)}",
            )
        return SlashCommandResult(
            handled=True,
            message=f"Status line: {', '.join(settings.items)}",
        )

    if name == "/model":
        if arg:
            state.model_override = arg
            return SlashCommandResult(handled=True, message=f"Model set to {arg}")
        current = state.model_override or config.model
        return SlashCommandResult(handled=True, message=f"Model: {current}")

    if name in ("/model-profile", "/modelprofile"):
        if arg:
            state.model_profile_override = arg
            return SlashCommandResult(
                handled=True, message=f"Model profile set to {arg}",
            )
        current = state.model_profile_override or "(default)"
        return SlashCommandResult(handled=True, message=f"Model profile: {current}")

    if name in ("/why-model", "/why"):
        from agent.model_routing import explain_model_routing

        task = arg or getattr(state, "last_routing_prompt", "") or ""
        if not task:
            return SlashCommandResult(
                handled=True,
                message="No prompt to analyze yet. Usage: /why-model [text]",
            )
        return SlashCommandResult(
            handled=True,
            message=explain_model_routing(
                task,
                cli_model_profile=state.model_profile_override,
                cwd=config.cwd,
            ),
        )

    if name == "/profile":
        if arg:
            state.profile_override = arg
            return SlashCommandResult(handled=True, message=f"Profile set to {arg}")
        current = state.profile_override or "(default)"
        return SlashCommandResult(handled=True, message=f"Profile: {current}")

    if name == "/plan":
        return _handle_plan_command(arg, state=state, config=config, thread=thread)

    if name == "/clear":
        return SlashCommandResult(
            handled=True,
            clear_session=True,
            message="Started a fresh session.",
            enter_chat=True,
        )

    if name in ("/cost", "/usage"):
        if thread is None:
            return SlashCommandResult(handled=True, message="No active thread yet.")
        summary = thread_cost_summary(thread)
        cost = summary.get("estimated_cost_usd", 0)
        return SlashCommandResult(
            handled=True,
            message=f"Thread cost ≈ ${cost:.4f} · {summary.get('turns', 0)} turns",
        )

    if name == "/thread":
        if thread is None:
            return SlashCommandResult(handled=True, message="No active thread yet.")
        return SlashCommandResult(
            handled=True,
            message=f"Thread {thread.id[:8]}… · {len(thread.turns)} turns",
        )

    if name == "/context":
        return _handle_context(arg, config=config, thread=thread)

    if name == "/compact":
        return _handle_compact(arg, config=config, store=store, thread=thread)

    return SlashCommandResult(
        handled=True,
        message="Unknown command. Try /help",
    )


def _handle_plan_command(
    arg: str,
    *,
    state: TuiSlashState,
    config: Config,
    thread: Thread | None,
) -> SlashCommandResult:
    if arg.lower() in ("on", "true", "1", "enable"):
        state.plan_mode = True
        return SlashCommandResult(handled=True, message="Plan mode ON (read-only tools)")
    if arg.lower() in ("off", "false", "0", "disable"):
        state.plan_mode = False
        return SlashCommandResult(handled=True, message="Plan mode OFF")

    allowed = ", ".join(config.plan_mode.allowed_tools)
    last_plan = None
    if thread:
        for turn in reversed(thread.turns):
            for item in turn.items:
                if item.type == "planProposal":
                    last_plan = item.text
                    break
            if last_plan:
                break
    lines = [
        f"Plan mode: {'on' if state.plan_mode else 'off'}",
        f"Allowed tools: {allowed}",
    ]
    if last_plan:
        lines.append(f"Last proposed plan: {plan_summary(last_plan)}")
    else:
        lines.append("Last proposed plan: (none)")
    return SlashCommandResult(handled=True, message="\n".join(lines))


def _handle_memories(
    arg: str,
    *,
    config: Config,
    thread: Thread | None,
) -> SlashCommandResult:
    from agent.memories import SuggestQueue, inject_memories_dry_run, memories_store_path

    settings = config.memories
    if arg.lower() == "inject":
        preview = inject_memories_dry_run(
            arg or (thread.title if thread and thread.title else "project"),
            settings,
            cwd=str(config.cwd),
        )
        return SlashCommandResult(handled=True, message=preview[:2000])
    pending = 0
    if settings.enabled:
        pending = SuggestQueue(config.cwd).pending_count()
    store_path = memories_store_path(settings)
    lines = [
        f"Memories: {'enabled' if settings.enabled else 'disabled'}",
        f"Store: {store_path}",
        f"Pending suggestions: {pending}",
    ]
    if arg.lower() in ("on", "enable", "true"):
        lines.append("Enable via [memories] enabled = true in config.toml")
    return SlashCommandResult(handled=True, message="\n".join(lines))


def _handle_context(
    arg: str,
    *,
    config: Config,
    thread: Thread | None,
) -> SlashCommandResult:
    import json

    from agent.context_meter import (
        build_context_snapshot,
        format_context_breakdown,
        snapshot_to_dict,
    )

    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    if arg.strip().lower() == "--json":
        return SlashCommandResult(
            handled=True,
            message=json.dumps(snapshot_to_dict(snap), indent=2),
        )
    return SlashCommandResult(
        handled=True,
        message=format_context_breakdown(snap),
    )


def _handle_compact(
    _arg: str,
    *,
    config: Config,
    store: ThreadStore,
    thread: Thread | None,
) -> SlashCommandResult:
    if thread is None:
        return SlashCommandResult(handled=True, message="No active thread to compact.")
    client = OpenRouterClient(config)
    result = force_compact_thread(thread, config, store, client)
    if not result.performed:
        return SlashCommandResult(
            handled=True,
            message="Nothing to compact (threshold not met or too few turns).",
        )
    msg = (
        f"Compacted: removed {result.removed_items} items, "
        f"tokens {result.estimated_tokens_before}→{result.estimated_tokens_after}"
    )
    if result.warning:
        msg += f"\n{result.warning}"
    return SlashCommandResult(handled=True, message=msg, reload_thread=True)
