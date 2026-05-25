from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+
    tomllib = None  # type: ignore[assignment]

from agent.exec_policy import ExecPolicyConfig, ExecPolicyMode, load_exec_policy_config
from agent.paths import default_config_path
from agent.sandbox.policy import SandboxMode
from agent.settings import (
    CompactionSettings,
    ExecutionSettings,
    IsolationSettings,
    MultiAgentSettings,
    OpenRouterSettings,
    RecordingSettings,
    WebSearchSettings,
    load_compaction_settings,
    load_execution_settings,
    load_isolation_settings,
    load_multi_agent_settings,
    load_openrouter_settings,
    load_recording_settings,
    load_web_search_settings,
)

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
DEFAULT_MAX_ROUNDS = 25
DEFAULT_COMMAND_TIMEOUT = 120
DEFAULT_MAX_TOOL_OUTPUT = 20_000
DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_COMPACTION_THRESHOLD = 0.7
ApprovalMode = Literal["interactive", "auto"]


@dataclass
class FileConfig:
    model: str | None = None
    approval_mode: ApprovalMode = "interactive"
    max_tool_rounds: int = DEFAULT_MAX_ROUNDS
    command_timeout_sec: int = DEFAULT_COMMAND_TIMEOUT
    max_tool_output_chars: int = DEFAULT_MAX_TOOL_OUTPUT
    default_cwd: str | None = None
    prefer_ripgrep: bool = True
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW
    compaction_threshold: float = DEFAULT_COMPACTION_THRESHOLD
    sandbox_mode: str | None = None
    tools: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> FileConfig:
        config_path = path or default_config_path()
        if not config_path.is_file():
            return cls()

        if tomllib is None:
            raise RuntimeError("tomllib is required for config file support")

        with config_path.open("rb") as f:
            data = tomllib.load(f)

        tools = data.get("tools", {})
        if not isinstance(tools, dict):
            tools = {}

        compaction = data.get("compaction", {})
        threshold = DEFAULT_COMPACTION_THRESHOLD
        if isinstance(compaction, dict) and "threshold" in compaction:
            threshold = float(compaction["threshold"])
        elif "compaction_threshold" in data:
            threshold = float(data["compaction_threshold"])

        return cls(
            model=data.get("model"),
            approval_mode=data.get("approval_mode", "interactive"),
            max_tool_rounds=int(data.get("max_tool_rounds", DEFAULT_MAX_ROUNDS)),
            command_timeout_sec=int(
                data.get("command_timeout_sec", DEFAULT_COMMAND_TIMEOUT)
            ),
            max_tool_output_chars=int(
                data.get("max_tool_output_chars", DEFAULT_MAX_TOOL_OUTPUT)
            ),
            default_cwd=data.get("default_cwd"),
            prefer_ripgrep=bool(tools.get("prefer_ripgrep", data.get("prefer_ripgrep", True))),
            context_window_tokens=int(
                data.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW)
            ),
            compaction_threshold=threshold,
            sandbox_mode=data.get("sandbox_mode"),
            tools=tools,
        )


@dataclass
class Config:
    cwd: Path
    model: str
    approval_mode: ApprovalMode = "interactive"
    max_rounds: int = DEFAULT_MAX_ROUNDS
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT
    max_tool_output: int = DEFAULT_MAX_TOOL_OUTPUT
    prefer_ripgrep: bool = True
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW
    compaction_threshold: float = DEFAULT_COMPACTION_THRESHOLD
    sandbox_mode: SandboxMode = SandboxMode.DANGER_FULL_ACCESS
    exec_policy: ExecPolicyConfig = field(default_factory=ExecPolicyConfig)
    compaction: CompactionSettings = field(default_factory=CompactionSettings)
    openrouter: OpenRouterSettings = field(default_factory=OpenRouterSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    isolation: IsolationSettings = field(default_factory=IsolationSettings)
    web_search: WebSearchSettings = field(default_factory=WebSearchSettings)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    multi_agent: MultiAgentSettings = field(default_factory=MultiAgentSettings)
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    config_path: Path | None = None
    skip_git_check: bool = False

    @property
    def auto_approve(self) -> bool:
        return self.approval_mode == "auto"

    @property
    def use_isolation(self) -> bool:
        if self.execution.backend == "docker":
            return False
        if self.sandbox_mode == SandboxMode.DANGER_FULL_ACCESS:
            return False
        return self.isolation.enabled

    @classmethod
    def resolve(
        cls,
        cwd: str | Path | None = None,
        *,
        model: str | None = None,
        auto_approve: bool | None = None,
        max_rounds: int | None = None,
        command_timeout: int | None = None,
        max_tool_output: int | None = None,
        prefer_ripgrep: bool | None = None,
        context_window_tokens: int | None = None,
        compaction_threshold: float | None = None,
        sandbox: str | None = None,
        execution_backend: str | None = None,
        docker_image: str | None = None,
        multi_agent: bool | None = None,
        skip_git_check: bool = False,
        config_path: Path | None = None,
    ) -> Config:
        resolved_config_path = config_path or default_config_path()
        file_cfg = FileConfig.load(resolved_config_path)

        resolved_cwd = cwd or file_cfg.default_cwd or Path.cwd()
        resolved_cwd = Path(resolved_cwd).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {resolved_cwd}")

        env_model = os.environ.get("OPENROUTER_MODEL")
        env_approval = os.environ.get("AGENT_APPROVAL_MODE")
        env_max_rounds = os.environ.get("AGENT_MAX_TOOL_ROUNDS")
        env_timeout = os.environ.get("AGENT_COMMAND_TIMEOUT_SEC")
        env_max_output = os.environ.get("AGENT_MAX_TOOL_OUTPUT_CHARS")
        env_prefer_rg = os.environ.get("AGENT_PREFER_RIPGREP")
        env_context_window = os.environ.get("AGENT_CONTEXT_WINDOW_TOKENS")
        env_compaction = os.environ.get("AGENT_COMPACTION_THRESHOLD")
        env_sandbox = os.environ.get("AGENT_SANDBOX_MODE")
        env_execution_backend = os.environ.get("AGENT_EXECUTION_BACKEND")

        api_key = os.environ.get("OPENROUTER_API_KEY")
        base_url = os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ).rstrip("/")

        resolved_model = model or env_model or file_cfg.model or DEFAULT_MODEL

        if auto_approve is not None:
            approval_mode: ApprovalMode = "auto" if auto_approve else "interactive"
        elif env_approval in ("auto", "interactive"):
            approval_mode = env_approval  # type: ignore[assignment]
        else:
            approval_mode = file_cfg.approval_mode

        resolved_max_rounds = max_rounds
        if resolved_max_rounds is None and env_max_rounds:
            resolved_max_rounds = int(env_max_rounds)
        if resolved_max_rounds is None:
            resolved_max_rounds = file_cfg.max_tool_rounds

        resolved_timeout = command_timeout
        if resolved_timeout is None and env_timeout:
            resolved_timeout = int(env_timeout)
        if resolved_timeout is None:
            resolved_timeout = file_cfg.command_timeout_sec

        resolved_max_output = max_tool_output
        if resolved_max_output is None and env_max_output:
            resolved_max_output = int(env_max_output)
        if resolved_max_output is None:
            resolved_max_output = file_cfg.max_tool_output_chars

        resolved_prefer_rg = prefer_ripgrep
        if resolved_prefer_rg is None and env_prefer_rg is not None:
            resolved_prefer_rg = env_prefer_rg.lower() in ("1", "true", "yes")
        if resolved_prefer_rg is None:
            resolved_prefer_rg = file_cfg.prefer_ripgrep

        resolved_context = context_window_tokens
        if resolved_context is None and env_context_window:
            resolved_context = int(env_context_window)
        if resolved_context is None:
            resolved_context = file_cfg.context_window_tokens

        compaction_cfg = load_compaction_settings(resolved_config_path)
        resolved_compaction = compaction_threshold
        if resolved_compaction is None and env_compaction:
            resolved_compaction = float(env_compaction)
        if resolved_compaction is None:
            resolved_compaction = compaction_cfg.threshold

        sandbox_value = sandbox or env_sandbox or file_cfg.sandbox_mode
        resolved_sandbox = SandboxMode.from_str(sandbox_value)

        exec_policy = load_exec_policy_config(resolved_config_path)
        openrouter = load_openrouter_settings(resolved_config_path)
        recording = load_recording_settings(resolved_config_path)
        isolation = load_isolation_settings(resolved_config_path)
        web_search = load_web_search_settings(resolved_config_path)
        execution_cfg = load_execution_settings(resolved_config_path)
        multi_agent_cfg = load_multi_agent_settings(resolved_config_path)

        backend_value = execution_backend or env_execution_backend or execution_cfg.backend
        execution_cfg.backend = backend_value
        if docker_image:
            execution_cfg.docker_image_override = docker_image
        if multi_agent is not None:
            multi_agent_cfg.enabled = multi_agent

        return cls(
            cwd=resolved_cwd,
            model=resolved_model,
            approval_mode=approval_mode,
            max_rounds=resolved_max_rounds,
            command_timeout=resolved_timeout,
            max_tool_output=resolved_max_output,
            prefer_ripgrep=resolved_prefer_rg,
            context_window_tokens=resolved_context,
            compaction_threshold=resolved_compaction,
            sandbox_mode=resolved_sandbox,
            exec_policy=exec_policy,
            compaction=compaction_cfg,
            openrouter=openrouter,
            recording=recording,
            isolation=isolation,
            web_search=web_search,
            execution=execution_cfg,
            multi_agent=multi_agent_cfg,
            openrouter_api_key=api_key,
            openrouter_base_url=base_url,
            config_path=resolved_config_path,
            skip_git_check=skip_git_check,
        )

    def require_api_key(self) -> str:
        if not self.openrouter_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required. Set it in your environment:\n"
                "  export OPENROUTER_API_KEY=your_key_here"
            )
        return self.openrouter_api_key

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "cwd": str(self.cwd),
            "model": self.model,
            "approval_mode": self.approval_mode,
            "max_tool_rounds": self.max_rounds,
            "command_timeout_sec": self.command_timeout,
            "max_tool_output_chars": self.max_tool_output,
            "prefer_ripgrep": self.prefer_ripgrep,
            "context_window_tokens": self.context_window_tokens,
            "compaction_threshold": self.compaction_threshold,
            "sandbox_mode": self.sandbox_mode.value,
            "exec_policy": self.exec_policy.mode.value,
            "exec_policy_allow_rules": len(self.exec_policy.rules.allow),
            "exec_policy_deny_rules": len(self.exec_policy.rules.deny),
            "compaction_enabled": self.compaction.enabled,
            "compaction_keep_recent_turns": self.compaction.keep_recent_turns,
            "openrouter_max_retries": self.openrouter.max_retries,
            "openrouter_retry_base_delay_sec": self.openrouter.retry_base_delay_sec,
            "openrouter_request_timeout_sec": self.openrouter.request_timeout_sec,
            "recording_enabled": self.recording.enabled,
            "recording_keep_last_runs_per_thread": self.recording.keep_last_runs_per_thread,
            "isolation_enabled": self.isolation.enabled,
            "isolation_effective": self.use_isolation,
            "web_search_enabled": self.web_search.enabled,
            "execution_backend": self.execution.backend,
            "execution_docker_image": self.execution.docker_image_override
            or self.execution.default_image,
            "execution_network": self.execution.network,
            "multi_agent_enabled": self.multi_agent.enabled,
            "multi_agent_max_workers": self.multi_agent.max_workers_per_turn,
            "openrouter_base_url": self.openrouter_base_url,
            "config_path": str(self.config_path) if self.config_path else None,
            "openrouter_api_key_set": bool(self.openrouter_api_key),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_display_dict(), indent=2)
