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
        self.thread = Thread(id=new_id(), cwd=str(cfg.cwd), model=cfg.model)
        self.store.create_thread(self.thread)
        return self.thread

    def handle_line(self, line: str) -> bool:
        """Returns False when REPL should exit."""
        stripped = line.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self._handle_command(stripped)
        thread = self._ensure_thread()
        cfg = self._resolve_config()
        emitter = build_event_emitter(self.output.handle)
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
        )
        for item in reversed(turn.items):
            if item.type == "agentMessage":
                self._print(item.text)
                break
        self._print(format_run_summary(turn))
        return True

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
            self._print("Compaction runs automatically when context threshold is reached")
            return True
        self._print(
            "Unknown command. Try /quit, /thread, /cost, /model, /profile, "
            "/model-profile, /skills, /usage"
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
    resume_turn: bool = False,
) -> None:
    cwd = (cwd or Path.cwd()).resolve()
    merged = merge_layered_config(cwd, cli_profile=profile, cli_model_profile=model_profile)
    kwargs = apply_merged_to_resolve_kwargs(merged)
    config = Config.resolve(cwd=cwd, **kwargs)
    store = ThreadStore()
    thread = None
    if resume_last:
        threads = [t for t in store.list_threads() if Path(t.cwd).resolve() == cwd]
        if threads:
            thread = max(threads, key=lambda t: t.updated_at)
    ReplSession(
        config=config,
        store=store,
        thread=thread,
        profile=profile,
        model_profile=model_profile,
        resume_turn=resume_turn,
    ).run()
