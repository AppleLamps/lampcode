from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.loop import run_turn
from agent.models import Thread, new_id
from agent.profiles import merge_layered_config, apply_merged_to_resolve_kwargs, thread_cost_summary
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
    ) -> None:
        self.config = config
        self.store = store or ThreadStore()
        self.thread = thread
        self._print = print_fn or (lambda s: sys.stdout.write(s + "\n"))
        self._input = input_fn or (lambda: input("> "))
        self.session = HarnessSession()
        self.model_override: str | None = None

    def _ensure_thread(self) -> Thread:
        if self.thread:
            return self.thread
        self.thread = Thread(id=new_id(), cwd=str(self.config.cwd), model=self.config.model)
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
        cfg = self.config
        if self.model_override:
            cfg = Config.resolve(cwd=cfg.cwd, model=self.model_override, config_path=cfg.config_path)
        turn = run_turn(thread, stripped, cfg, self.store, harness_session=self.session)
        for item in reversed(turn.items):
            if item.type == "agentMessage":
                self._print(item.text)
                break
        cost = thread_cost_summary(thread)
        if cost.get("estimated_cost_usd"):
            self._print(f"[cost: ${cost['estimated_cost_usd']:.4f} | model: {turn.usage.model_used or cfg.model}]")
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
        if name == "/cost":
            if not self.thread:
                self._print("No thread yet")
            else:
                import json

                self._print(json.dumps(thread_cost_summary(self.thread), indent=2))
            return True
        if name == "/model":
            if arg:
                self.model_override = arg
                self._print(f"Model set to {arg}")
            else:
                self._print(self.model_override or self.config.model)
            return True
        if name == "/clear":
            self.thread = None
            self._print("Started fresh thread context")
            return True
        if name == "/skills":
            from agent.skills.discovery import discover_skills

            skills = discover_skills(self.config.cwd)
            self._print(", ".join(s.name for s in skills) or "(none)")
            return True
        if name == "/compact":
            self._print("Compaction runs automatically when context threshold is reached")
            return True
        self._print(f"Unknown command {name}. Try /quit, /thread, /cost, /model, /skills")
        return True

    def run(self) -> None:
        self._print("agent repl — /quit to exit")
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
    ReplSession(config=config, store=store, thread=thread).run()
