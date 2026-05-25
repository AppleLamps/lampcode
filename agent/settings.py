from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

from agent.paths import default_config_path


@dataclass
class SkillsConfig:
    max_active: int = 3
    max_body_chars: int = 4000
    enable_user_skills: bool = True
    enable_project_skills: bool = True
    project_rules_max_chars: int = 8000


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    enabled: bool = True
    require_approval: bool = True
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class McpSettings:
    startup_timeout_sec: int = 30
    tool_name_prefix: bool = True
    tool_call_timeout_sec: int = 60


@dataclass
class McpConfig:
    servers: dict[str, McpServerConfig] = field(default_factory=dict)
    settings: McpSettings = field(default_factory=McpSettings)


@dataclass
class CompactionSettings:
    enabled: bool = True
    keep_recent_turns: int = 2
    threshold: float = 0.7
    summary_max_chars: int = 8000


@dataclass
class OpenRouterSettings:
    max_retries: int = 3
    retry_base_delay_sec: float = 1.0
    request_timeout_sec: int = 120


DEFAULT_ALLOWED_ENV = [
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "LANG",
    "PYTHONIOENCODING",
    "COMSPEC",
    "WINDIR",
]


@dataclass
class RecordingSettings:
    enabled: bool = True
    keep_last_runs_per_thread: int = 50


@dataclass
class IsolationSettings:
    enabled: bool = True
    strip_env: bool = True
    kill_process_tree_on_timeout: bool = True
    clear_network_env_hints: bool = True
    allowed_env_vars: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_ENV))


@dataclass
class WebSearchSettings:
    enabled: bool = False
    provider: str = "duckduckgo"
    max_results: int = 5
    timeout_sec: int = 15


def _load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_compaction_settings(path: Path | None = None) -> CompactionSettings:
    data = _load_toml(path or default_config_path())
    compaction = data.get("compaction", {})
    if not isinstance(compaction, dict):
        compaction = {}
    return CompactionSettings(
        enabled=bool(compaction.get("enabled", True)),
        keep_recent_turns=int(compaction.get("keep_recent_turns", 2)),
        threshold=float(compaction.get("threshold", data.get("compaction_threshold", 0.7))),
        summary_max_chars=int(compaction.get("summary_max_chars", 8000)),
    )


def load_openrouter_settings(path: Path | None = None) -> OpenRouterSettings:
    data = _load_toml(path or default_config_path())
    or_cfg = data.get("openrouter", {})
    if not isinstance(or_cfg, dict):
        or_cfg = {}
    return OpenRouterSettings(
        max_retries=int(or_cfg.get("max_retries", 3)),
        retry_base_delay_sec=float(or_cfg.get("retry_base_delay_sec", 1.0)),
        request_timeout_sec=int(or_cfg.get("request_timeout_sec", 120)),
    )


def load_recording_settings(path: Path | None = None) -> RecordingSettings:
    data = _load_toml(path or default_config_path())
    rec = data.get("recording", {})
    if not isinstance(rec, dict):
        rec = {}
    return RecordingSettings(
        enabled=bool(rec.get("enabled", True)),
        keep_last_runs_per_thread=int(rec.get("keep_last_runs_per_thread", 50)),
    )


def load_isolation_settings(path: Path | None = None) -> IsolationSettings:
    data = _load_toml(path or default_config_path())
    iso = data.get("isolation", {})
    if not isinstance(iso, dict):
        iso = {}
    allowed = iso.get("allowed_env_vars", DEFAULT_ALLOWED_ENV)
    if not isinstance(allowed, list):
        allowed = list(DEFAULT_ALLOWED_ENV)
    return IsolationSettings(
        enabled=bool(iso.get("enabled", True)),
        strip_env=bool(iso.get("strip_env", True)),
        kill_process_tree_on_timeout=bool(iso.get("kill_process_tree_on_timeout", True)),
        clear_network_env_hints=bool(iso.get("clear_network_env_hints", True)),
        allowed_env_vars=[str(v) for v in allowed],
    )


def load_web_search_settings(path: Path | None = None) -> WebSearchSettings:
    data = _load_toml(path or default_config_path())
    ws = data.get("web_search", {})
    if not isinstance(ws, dict):
        ws = {}
    return WebSearchSettings(
        enabled=bool(ws.get("enabled", False)),
        provider=str(ws.get("provider", "duckduckgo")),
        max_results=int(ws.get("max_results", 5)),
        timeout_sec=int(ws.get("timeout_sec", 15)),
    )


def _parse_mcp_servers(data: dict[str, Any]) -> dict[str, McpServerConfig]:
    servers: dict[str, McpServerConfig] = {}
    raw = data.get("mcp_servers", {})
    if not isinstance(raw, dict):
        return servers
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        servers[name] = McpServerConfig(
            name=name,
            command=str(cfg.get("command", "")),
            args=list(cfg.get("args", [])),
            enabled=bool(cfg.get("enabled", True)),
            require_approval=bool(cfg.get("require_approval", True)),
            env=dict(cfg.get("env", {})),
        )
    return servers


def _parse_mcp_settings(data: dict[str, Any]) -> McpSettings:
    mcp = data.get("mcp", {})
    if not isinstance(mcp, dict):
        return McpSettings()
    return McpSettings(
        startup_timeout_sec=int(mcp.get("startup_timeout_sec", 30)),
        tool_name_prefix=bool(mcp.get("tool_name_prefix", True)),
        tool_call_timeout_sec=int(mcp.get("tool_call_timeout_sec", 60)),
    )


def _parse_skills_config(data: dict[str, Any]) -> SkillsConfig:
    skills = data.get("skills", {})
    if not isinstance(skills, dict):
        return SkillsConfig()
    return SkillsConfig(
        max_active=int(skills.get("max_active", 3)),
        max_body_chars=int(skills.get("max_body_chars", 4000)),
        enable_user_skills=bool(skills.get("enable_user_skills", True)),
        enable_project_skills=bool(skills.get("enable_project_skills", True)),
        project_rules_max_chars=int(skills.get("project_rules_max_chars", 8000)),
    )


def load_mcp_config(
    user_config_path: Path | None = None,
    project_cwd: Path | None = None,
) -> McpConfig:
    if tomllib is None:
        return McpConfig()

    merged: dict[str, Any] = {}
    user_path = user_config_path or default_config_path()
    if user_path.is_file():
        with user_path.open("rb") as f:
            merged = tomllib.load(f)

    if project_cwd:
        project_mcp = project_cwd / ".agent-cli" / "mcp.toml"
        if project_mcp.is_file():
            with project_mcp.open("rb") as f:
                project_data = tomllib.load(f)
            user_servers = merged.get("mcp_servers", {})
            project_servers = project_data.get("mcp_servers", {})
            if isinstance(user_servers, dict) and isinstance(project_servers, dict):
                merged_servers = {**user_servers, **project_servers}
                merged["mcp_servers"] = merged_servers
            if "mcp" in project_data:
                merged["mcp"] = {**merged.get("mcp", {}), **project_data["mcp"]}

    return McpConfig(
        servers=_parse_mcp_servers(merged),
        settings=_parse_mcp_settings(merged),
    )


def load_skills_config(user_config_path: Path | None = None) -> SkillsConfig:
    if tomllib is None:
        return SkillsConfig()
    path = user_config_path or default_config_path()
    if not path.is_file():
        return SkillsConfig()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return _parse_skills_config(data)
