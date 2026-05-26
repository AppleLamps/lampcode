from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.events import build_event_emitter
from agent.loop import run_turn
from agent.models import Thread, new_id
from agent.output_handler import OutputHandler, format_run_summary
from agent.repl_completer import install_repl_completer
from agent.profiles import (
    apply_merged_to_resolve_kwargs,
    merge_layered_config,
    thread_cost_summary,
)
from agent.session import HarnessSession
from agent.store import ThreadStore


ReplHandler = Callable[[str], None]


class ReplSession:
    def __init__(
        self,
        *,
        config: Config,
        store: ThreadStore | None = None,
        thread: Thread | None = None,
        print_fn: ReplHandler | None = None,
        input_fn: Callable[[], str] | None = None,
        profile: str | None = None,
        model_profile: str | None = None,
        resume_turn: bool = False,
        ephemeral: bool = False,
    ) -> None:
        self.config = config
        self.store = store or ThreadStore()
        self.thread = thread
        self._print = print_fn or (lambda s: sys.stdout.write(s + "\n"))
        self._input = input_fn or (lambda: input("> "))
        self.session = HarnessSession()
        self.model_override: str | None = None
        self.profile_override = profile
        self.model_profile_override = model_profile
        self.resume_turn = resume_turn
        self._pending_resume_turn = resume_turn
        self.ephemeral = ephemeral
        self.plan_mode = False
        self.output = OutputHandler(quiet_tools=False)
        self._completer_installed = install_repl_completer(config.cwd)

    def _resolve_config(self) -> Config:
        merged = merge_layered_config(
            self.config.cwd,
            cli_profile=self.profile_override,
            cli_model_profile=self.model_profile_override,
            cli_model=self.model_override,
        )
        kwargs = apply_merged_to_resolve_kwargs(merged)
        if self.config.auto_approve:
            kwargs["auto_approve"] = True
        return Config.resolve(cwd=self.config.cwd, **kwargs)

    def _ensure_thread(self) -> Thread:
        if self.thread:
            return self.thread
        cfg = self._resolve_config()
        self.thread = Thread(
            id=f"ephemeral-{new_id()}" if self.ephemeral else new_id(),
            cwd=str(cfg.cwd),
            model=cfg.model,
        )
        if not self.ephemeral:
            self.store.create_thread(self.thread)
        return self.thread

    def _load_thread_by_id(self, thread_id: str) -> Thread | None:
        try:
            return self.store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [t for t in self.store.list_threads() if t.id.startswith(thread_id)]
            if len(matches) == 1:
                return matches[0]
        return None

    def handle_line(self, line: str) -> bool:
        """Returns False when REPL should exit."""
        stripped = line.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self._handle_command(stripped)
        thread = self._ensure_thread()
        cfg = self._resolve_config()
        emitter = build_event_emitter(
            self.output.handle,
            recording=cfg.recording.enabled,
            recording_keep=cfg.recording.keep_last_runs_per_thread,
            action_log=cfg.action_log,
            project_cwd=cfg.cwd,
            model=cfg.model,
        )
        resume_cp = None
        prompt = stripped
        if self._pending_resume_turn:
            from agent.turn_checkpoint import TurnCheckpointStore

            store_cp = TurnCheckpointStore(Path(cfg.turn_checkpoint.dir).expanduser())
            resume_cp = store_cp.find_latest(thread.id)
            if resume_cp:
                prompt = resume_cp.user_text or stripped
                self._print(f"[resume] continuing turn {resume_cp.turn_id[:8]}…")
            self._pending_resume_turn = False
        turn = run_turn(
            thread,
            prompt,
            cfg,
            self.store,
            events=emitter,
            harness_session=self.session,
            session_auto_approve=cfg.auto_approve,
            resume_checkpoint=resume_cp,
            plan_mode=self.plan_mode,
        )
        for item in reversed(turn.items):
            if item.type == "agentMessage":
                self._print(item.text)
                break
        self._print(format_run_summary(turn))
        return True

    def _prompt_input(self, prompt: str) -> str:
        self._print(prompt.rstrip())
        return self._input().strip()

    def _handle_command(self, cmd: str) -> bool:
        parts = cmd.split(maxsplit=1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if name in ("/quit", "/exit", "/q"):
            return False
        if name == "/thread":
            t = self._ensure_thread()
            self._print(f"thread {t.id} ({len(t.turns)} turns)")
            return True
        if name == "/cost" or name == "/usage":
            if not self.thread:
                self._print("No thread yet")
            else:
                self._print(json.dumps(thread_cost_summary(self.thread), indent=2))
            return True
        if name == "/model":
            if arg:
                self.model_override = arg
                self._print(f"Model set to {arg}")
            else:
                self._print(self.model_override or self.config.model)
            return True
        if name == "/profile":
            if arg:
                self.profile_override = arg
                self._print(f"Profile set to {arg}")
            else:
                self._print(self.profile_override or "(default)")
            return True
        if name in ("/model-profile", "/modelprofile"):
            if arg:
                self.model_profile_override = arg
                self._print(f"Model profile set to {arg}")
            else:
                self._print(self.model_profile_override or "(default)")
            return True
        if name == "/clear":
            self.thread = None
            self._print("Started fresh thread context")
            return True
        if name == "/skills":
            from agent.skills.discovery import discover_skills

            skills = discover_skills(self.config.cwd)
            names = [s.name for s in skills]
            if arg:
                prefix = arg.lower()
                names = [n for n in names if n.lower().startswith(prefix)]
            self._print(", ".join(names) or "(none)")
            return True
        if name == "/compact":
            thread = self.thread
            if thread is None:
                self._print("No active thread to compact")
                return True
            cfg = self._resolve_config()
            from agent.compaction import force_compact_thread
            from model.openrouter import OpenRouterClient

            client = OpenRouterClient(cfg)
            result = force_compact_thread(thread, cfg, self.store, client)
            if result.performed:
                msg = (
                    f"Compacted: removed {result.removed_items} items, "
                    f"tokens {result.estimated_tokens_before}→{result.estimated_tokens_after}"
                )
                if result.warning:
                    msg += f"\n{result.warning}"
                self._print(msg)
            else:
                self._print("Nothing to compact (threshold not met or too few turns)")
            return True
        if name == "/plan":
            cfg = self._resolve_config()
            if arg.lower() in ("on", "true", "1", "enable"):
                self.plan_mode = True
                self._print("Plan mode ON (read-only tools)")
            elif arg.lower() in ("off", "false", "0", "disable"):
                self.plan_mode = False
                self._print("Plan mode OFF")
            else:
                from agent.plan_mode import plan_summary

                allowed = ", ".join(cfg.plan_mode.allowed_tools)
                last_plan = None
                if self.thread:
                    for turn in reversed(self.thread.turns):
                        for item in turn.items:
                            if item.type == "planProposal":
                                last_plan = item.text
                                break
                        if last_plan:
                            break
                self._print(f"Plan mode: {'on' if self.plan_mode else 'off'}")
                self._print(f"Allowed tools: {allowed}")
                if last_plan:
                    self._print(f"Last proposed plan: {plan_summary(last_plan)}")
                else:
                    self._print("Last proposed plan: (none)")
            return True
        if name == "/resume":
            from agent.threads_picker import pick_thread_or_last

            picked = pick_thread_or_last(
                self.store,
                self.config.cwd,
                use_last=arg.lower() == "last",
                input_fn=self._prompt_input,
            )
            if picked is None:
                self._print("No thread selected")
            else:
                self.thread = picked
                self._print(f"Resumed thread {picked.id[:8]}… ({picked.display_label()})")
            return True
        if name == "/fork":
            from agent.threads_picker import pick_thread

            source = self.thread
            if arg:
                loaded = self._load_thread_by_id(arg)
                if loaded:
                    source = loaded
            if source is None:
                source = pick_thread(self.store, self.config.cwd, input_fn=self._prompt_input)
            if source is None:
                self._print("No thread selected")
            else:
                forked = self.store.fork_thread(source)
                self.thread = forked
                self._print(f"Forked to {forked.id[:8]}…")
            return True
        if name == "/ephemeral":
            if arg.lower() in ("on", "true", "1", "enable"):
                self.ephemeral = True
                self.thread = None
                self._print("Ephemeral mode ON (threads not persisted)")
            elif arg.lower() in ("off", "false", "0", "disable"):
                self.ephemeral = False
                self._print("Ephemeral mode OFF")
            else:
                self._print(f"Ephemeral mode: {'on' if self.ephemeral else 'off'}")
            return True
        self._print(
            "Unknown command. Try /quit, /thread, /cost, /model, /profile, "
            "/model-profile, /skills, /usage, /plan, /resume, /fork, /ephemeral"
        )
        return True

    def run(self) -> None:
        hint = "agent repl — /quit to exit"
        if self._completer_installed:
            hint += " (Tab completes @skills and /commands)"
        self._print(hint)
        while True:
            try:
                line = self._input()
            except (EOFError, KeyboardInterrupt):
                self._print("")
                break
            if not self.handle_line(line):
                break


def run_repl(
    *,
    cwd: Path | None = None,
    profile: str | None = None,
    model_profile: str | None = None,
    resume_last: bool = False,
    resume: bool = False,
    resume_turn: bool = False,
    ephemeral: bool = False,
) -> None:
    cwd = (cwd or Path.cwd()).resolve()
    merged = merge_layered_config(cwd, cli_profile=profile, cli_model_profile=model_profile)
    kwargs = apply_merged_to_resolve_kwargs(merged)
    config = Config.resolve(cwd=cwd, **kwargs)
    store = ThreadStore.ephemeral() if ephemeral else ThreadStore()
    thread = None
    if resume_last:
        threads = [t for t in store.list_threads() if Path(t.cwd).resolve() == cwd]
        if threads:
            thread = max(threads, key=lambda t: t.updated_at)
    elif resume:
        from agent.threads_picker import pick_thread

        thread = pick_thread(store, cwd)
    ReplSession(
        config=config,
        store=store,
        thread=thread,
        profile=profile,
        model_profile=model_profile,
        resume_turn=resume_turn,
        ephemeral=ephemeral,
    ).run()
