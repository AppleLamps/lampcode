from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.events import EventEmitter
from agent.settings import HooksSettings, load_hooks_settings

SUPPORTED_HOOK_EVENTS = [
    "on_session_start",
    "on_user_prompt_submit",
    "on_permission_request",
    "on_pre_tool_use",
    "on_tool_pending",
    "on_turn_completed",
    "on_pre_compact",
    "on_post_compact",
]


def _hooks_paths(cwd: Path) -> list[Path]:
    paths: list[Path] = []
    project = cwd / ".agent-cli" / "hooks.json"
    user = Path.home() / ".agent-cli" / "hooks.json"
    if user.is_file():
        paths.append(user)
    if project.is_file():
        paths.append(project)
    return paths


def load_hooks_config(cwd: Path) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    for path in _hooks_paths(cwd):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key, hooks in data.items():
            if isinstance(hooks, list):
                merged[key] = list(hooks)
    return merged


@dataclass
class HookResult:
    errors: list[str] = field(default_factory=list)
    context_append: str = ""
    block: bool = False
    block_reason: str = ""


@dataclass
class HooksRunner:
    cwd: Path
    settings: HooksSettings
    hooks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    emitter: EventEmitter | None = None
    thread_id: str | None = None
    turn_id: str | None = None

    @classmethod
    def from_cwd(
        cls,
        cwd: Path,
        *,
        config_path: Path | None = None,
        project_path: Path | None = None,
        emitter: EventEmitter | None = None,
    ) -> HooksRunner:
        settings = load_hooks_settings(config_path, project_path=project_path)
        hooks = load_hooks_config(cwd)
        return cls(cwd=cwd, settings=settings, hooks=hooks, emitter=emitter)

    def run(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        tool_name: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[str]:
        return self.run_event(
            event_name,
            payload,
            tool_name=tool_name,
            thread_id=thread_id,
            turn_id=turn_id,
        ).errors

    def run_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        tool_name: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
    ) -> HookResult:
        errors: list[str] = []
        context_parts: list[str] = []
        block = False
        block_reason = ""
        hooks = self.hooks.get(event_name, [])
        if not hooks:
            return HookResult()
        tid = thread_id or self.thread_id
        trid = turn_id or self.turn_id
        for hook in hooks:
            cmd = hook.get("command")
            if not cmd:
                continue
            timeout = int(hook.get("timeout_sec", 30))
            env = os.environ.copy()
            env["AGENT_HOOK_EVENT"] = event_name
            env["AGENT_THREAD_ID"] = tid or ""
            env["AGENT_TURN_ID"] = trid or ""
            if tool_name:
                env["AGENT_TOOL_NAME"] = tool_name
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=self.cwd,
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                if proc.returncode != 0:
                    msg = proc.stderr.strip() or f"exit {proc.returncode}"
                    errors.append(msg)
                    if self.emitter:
                        from agent.events import AgentEvent

                        self.emitter.emit(
                            AgentEvent(
                                "hook.failed",
                                thread_id=tid,
                                turn_id=trid,
                                data={"event": event_name, "command": cmd, "error": msg},
                            )
                        )
                    continue
                parsed = _parse_hook_stdout(proc.stdout)
                if parsed.get("context_append"):
                    context_parts.append(str(parsed["context_append"]))
                if parsed.get("decision") == "block":
                    block = True
                    block_reason = str(parsed.get("reason") or "blocked by hook")
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc))
                if self.emitter:
                    from agent.events import AgentEvent

                    self.emitter.emit(
                        AgentEvent(
                            "hook.failed",
                            thread_id=tid,
                            turn_id=trid,
                            data={"event": event_name, "command": cmd, "error": str(exc)},
                        )
                    )
        if errors and self.settings.fail_on_error:
            raise RuntimeError(f"Hook failed: {errors[0]}")
        return HookResult(
            errors=errors,
            context_append="\n".join(p for p in context_parts if p),
            block=block,
            block_reason=block_reason,
        )


def _parse_hook_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}
