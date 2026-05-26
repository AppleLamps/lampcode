from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.exec_policy import load_allow_prefixes, project_exec_policy_path
from agent.execution.shell_session import pty_support_status
from agent.hooks.runner import SUPPORTED_HOOK_EVENTS, load_hooks_config
from agent.memories import SuggestQueue
from agent.paths import default_config_path
from agent.settings import load_mcp_config, load_skills_config
from agent.skills.discovery import discover_skills


def build_doctor_report(config: Config | None = None) -> list[dict[str, str]]:
    cfg = config or Config.resolve()
    checks: list[dict[str, str]] = []

    def row(check: str, status: str, detail: str) -> dict[str, str]:
        return {
            "check": check,
            "status": status,
            "detail": detail.replace("\n", " ").replace("\r", " ").strip(),
        }

    api_key = cfg.openrouter_api_key
    checks.append(
        row(
            "OPENROUTER_API_KEY",
            "set" if api_key else "MISSING",
            "set" if api_key else "missing",
        )
    )
    checks.append(row("git", _found("git"), _which("git") or "not found"))
    checks.append(row("ripgrep", _found("rg"), _which("rg") or "not found"))
    checks.append(row("model", "ok", cfg.model))

    sandbox = cfg.sandbox_mode.value
    if sandbox == "danger-full-access":
        checks.append(
            row(
                "sandbox",
                "WARN",
                f"{sandbox} — tools can modify files outside workspace; prefer workspace-write",
            )
        )
    else:
        checks.append(row("sandbox", "ok", sandbox))

    pty = pty_support_status()
    checks.append(
        row(
            "shell",
            "ok",
            (
                f"configured={cfg.shell.backend}, probe={pty.get('backend')}, "
                f"conpty={pty.get('conpty_available')}"
            ),
        )
    )

    ws = cfg.web_search
    ws_key = "n/a"
    if ws.provider != "duckduckgo":
        ws_key = "set" if (
            ws.api_key or (ws.api_key_env and os.environ.get(ws.api_key_env))
        ) else "missing"
    checks.append(row("web_search", "ok", f"provider={ws.provider} key={ws_key}"))

    exec_path = project_exec_policy_path(cfg.cwd)
    prefix_count = len(load_allow_prefixes(cfg.cwd))
    checks.append(
        row(
            "exec_policy",
            "ok" if exec_path.is_file() or prefix_count else "default",
            f"file={'yes' if exec_path.is_file() else 'no'}, allow_prefixes={prefix_count}",
        )
    )

    hooks = load_hooks_config(cfg.cwd)
    hook_detail = ", ".join(
        f"{ev}={len(hooks.get(ev, []))}" for ev in SUPPORTED_HOOK_EVENTS
    )
    checks.append(row("hooks", "ok", hook_detail or "none configured"))

    pending = 0
    if cfg.memories.enabled:
        pending = SuggestQueue(cfg.cwd).pending_count()
    checks.append(
        row(
            "memories",
            "enabled" if cfg.memories.enabled else "disabled",
            f"pending_suggestions={pending}",
        )
    )

    try:
        from agent.sandbox.kernel.doctor import probe_capabilities

        probe = probe_capabilities(cfg.sandbox_kernel)
        checks.append(
            row(
                "kernel_sandbox",
                "available" if probe.get("available") else "unavailable",
                str(probe.get("note", probe.get("backend", ""))),
            )
        )
    except Exception as exc:
        checks.append(row("kernel_sandbox", "unavailable", str(exc)))

    config_path = default_config_path()
    checks.append(
        row(
            "config_file",
            "found" if config_path.exists() else "missing",
            str(config_path),
        )
    )

    mcp_cfg = load_mcp_config(config_path, cfg.cwd)
    enabled_mcp = sum(1 for s in mcp_cfg.servers.values() if s.enabled)
    checks.append(row("mcp_servers", "ok", f"enabled={enabled_mcp}"))

    skills_cfg = load_skills_config(config_path)
    skill_count = len(
        discover_skills(
            cfg.cwd,
            enable_project=skills_cfg.enable_project_skills,
            enable_user=skills_cfg.enable_user_skills,
        )
    )
    checks.append(row("skills", "ok", f"discovered={skill_count}"))

    return checks


def _which(name: str) -> str | None:
    return shutil.which(name)


def _found(name: str) -> str:
    return "found" if shutil.which(name) else "not found"
