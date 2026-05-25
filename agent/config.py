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
from agent.profiles import merge_layered_config, apply_merged_to_resolve_kwargs, project_config_path
from agent.sandbox.policy import SandboxMode
from agent.settings import (
    CompactionSettings,
    ExecutionSettings,
    IsolationSettings,
    MultiAgentSettings,
    OpenRouterSettings,
    RecordingSettings,
    TurnCheckpointSettings,
    ShellSettings,
    HooksSettings,
    MemoriesSettings,
    PlanModeSettings,
    SandboxProfileSettings,
    TelemetrySettings,
    WebSearchSettings,
    load_compaction_settings,
    load_execution_settings,
    load_isolation_settings,
    load_multi_agent_settings,
    load_openrouter_settings,
    load_recording_settings,
    load_turn_checkpoint_settings,
    load_shell_settings,
    load_hooks_settings,
    load_memories_settings,
    load_plan_mode_settings,
    load_sandbox_profile_settings,
    load_kernel_sandbox_settings,
    load_telemetry_settings,
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
    turn_checkpoint: TurnCheckpointSettings = field(default_factory=TurnCheckpointSettings)
    shell: ShellSettings = field(default_factory=ShellSettings)
    hooks: HooksSettings = field(default_factory=HooksSettings)
    memories: MemoriesSettings = field(default_factory=MemoriesSettings)
    plan_mode: PlanModeSettings = field(default_factory=PlanModeSettings)
    isolation: IsolationSettings = field(default_factory=IsolationSettings)
    web_search: WebSearchSettings = field(default_factory=WebSearchSettings)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    multi_agent: MultiAgentSettings = field(default_factory=MultiAgentSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    sandbox_profiles: SandboxProfileSettings = field(default_factory=SandboxProfileSettings)
    sandbox_kernel: Any = field(default_factory=lambda: __import__(
        "agent.sandbox.kernel", fromlist=["KernelSandboxSettings"]
    ).KernelSandboxSettings())
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    config_path: Path | None = None
    skip_git_check: bool = False
    force_sync: bool = False
    reasoning_effort: str | None = None
    model_profile_fallbacks: list[str] = field(default_factory=list)

    @property
    def auto_approve(self) -> bool:
        return self.approval_mode == "auto"

    @property
    def use_isolation(self) -> bool:
        if self.execution.backend in ("docker", "ssh"):
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
        ssh_host: str | None = None,
        ssh_user: str | None = None,
        ssh_identity_file: str | None = None,
        sync_mode: str | None = None,
        force_sync: bool = False,
        multi_agent: bool | None = None,
        skip_git_check: bool = False,
        config_path: Path | None = None,
        profile: str | None = None,
        model_profile: str | None = None,
    ) -> Config:
        resolved_config_path = config_path or default_config_path()

        resolved_cwd = cwd
        if resolved_cwd is None:
            file_cfg_early = FileConfig.load(resolved_config_path)
            resolved_cwd = file_cfg_early.default_cwd or Path.cwd()
        resolved_cwd = Path(resolved_cwd).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {resolved_cwd}")

        layered = merge_layered_config(
            resolved_cwd,
            cli_profile=profile,
            cli_model_profile=model_profile,
            cli_model=model,
            user_path=resolved_config_path,
        )
        layered_kwargs = apply_merged_to_resolve_kwargs(layered)
        if model is None and layered.get("model"):
            model = str(layered["model"])
        if max_rounds is None and layered_kwargs.get("max_rounds") is not None:
            max_rounds = int(layered_kwargs["max_rounds"])
        if auto_approve is None and "auto_approve" in layered_kwargs:
            auto_approve = bool(layered_kwargs["auto_approve"])
        if sandbox is None and layered_kwargs.get("sandbox"):
            sandbox = str(layered_kwargs["sandbox"])

        project_path = project_config_path(resolved_cwd)
        file_cfg = FileConfig.load(resolved_config_path)
        if project_path.is_file():
            project_cfg = FileConfig.load(project_path)
            if project_cfg.model:
                file_cfg.model = project_cfg.model
            if project_cfg.sandbox_mode:
                file_cfg.sandbox_mode = project_cfg.sandbox_mode
            if project_cfg.approval_mode != "interactive":
                file_cfg.approval_mode = project_cfg.approval_mode
            if project_cfg.max_tool_rounds != DEFAULT_MAX_ROUNDS:
                file_cfg.max_tool_rounds = project_cfg.max_tool_rounds
        if layered.get("approval_mode") in ("auto", "interactive"):
            file_cfg.approval_mode = layered["approval_mode"]  # type: ignore[assignment]
        if layered.get("sandbox_mode"):
            file_cfg.sandbox_mode = str(layered["sandbox_mode"])
        if layered.get("max_tool_rounds") is not None:
            file_cfg.max_tool_rounds = int(layered["max_tool_rounds"])
        if layered.get("model"):
            file_cfg.model = str(layered["model"])

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
        env_ssh_host = os.environ.get("AGENT_SSH_HOST")
        env_ssh_user = os.environ.get("AGENT_SSH_USER")
        env_ssh_identity = os.environ.get("AGENT_SSH_IDENTITY_FILE")

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
        openrouter = load_openrouter_settings(resolved_config_path, project_path=project_path)
        recording = load_recording_settings(resolved_config_path)
        turn_checkpoint = load_turn_checkpoint_settings(
            resolved_config_path, project_path=project_path
        )
        shell_cfg = load_shell_settings(resolved_config_path, project_path=project_path)
        hooks_cfg = load_hooks_settings(resolved_config_path, project_path=project_path)
        memories_cfg = load_memories_settings(resolved_config_path, project_path=project_path)
        plan_mode_cfg = load_plan_mode_settings(resolved_config_path, project_path=project_path)
        isolation = load_isolation_settings(resolved_config_path)
        web_search = load_web_search_settings(resolved_config_path)
        execution_cfg = load_execution_settings(resolved_config_path)
        multi_agent_cfg = load_multi_agent_settings(resolved_config_path)
        telemetry_cfg = load_telemetry_settings(resolved_config_path)
        sandbox_profiles_cfg = load_sandbox_profile_settings(resolved_config_path)
        sandbox_kernel_cfg = load_kernel_sandbox_settings(resolved_config_path)

        if os.environ.get("AGENT_OTEL_ENABLED", "").strip().lower() in ("1", "true", "yes"):
            telemetry_cfg.enabled = True
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            telemetry_cfg.otlp_endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]

        backend_value = execution_backend or env_execution_backend or execution_cfg.backend
        execution_cfg.backend = backend_value
        if docker_image:
            execution_cfg.docker_image_override = docker_image
        if ssh_host or env_ssh_host:
            execution_cfg.ssh.host = ssh_host or env_ssh_host or execution_cfg.ssh.host
        if ssh_user or env_ssh_user:
            execution_cfg.ssh.user = ssh_user or env_ssh_user or execution_cfg.ssh.user
        if ssh_identity_file or env_ssh_identity:
            execution_cfg.ssh.identity_file = (
                ssh_identity_file or env_ssh_identity or execution_cfg.ssh.identity_file
            )
        if sync_mode:
            execution_cfg.ssh.sync_mode = sync_mode
            execution_cfg.ssh.sync_enabled = True
        if multi_agent is not None:
            multi_agent_cfg.enabled = multi_agent

        profile_fallbacks = layered.get("_model_profile_fallbacks", [])
        reasoning = layered.get("_reasoning_effort") or None
        if reasoning == "":
            reasoning = None

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
            turn_checkpoint=turn_checkpoint,
            shell=shell_cfg,
            hooks=hooks_cfg,
            memories=memories_cfg,
            plan_mode=plan_mode_cfg,
            isolation=isolation,
            web_search=web_search,
            execution=execution_cfg,
            multi_agent=multi_agent_cfg,
            telemetry=telemetry_cfg,
            sandbox_profiles=sandbox_profiles_cfg,
            sandbox_kernel=sandbox_kernel_cfg,
            openrouter_api_key=api_key,
            openrouter_base_url=base_url,
            config_path=resolved_config_path,
            skip_git_check=skip_git_check,
            force_sync=force_sync,
            reasoning_effort=str(reasoning) if reasoning else None,
            model_profile_fallbacks=[str(x) for x in profile_fallbacks] if isinstance(profile_fallbacks, list) else [],
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
            "multi_agent_max_depth": self.multi_agent.max_worker_depth,
            "multi_agent_max_concurrent": self.multi_agent.max_concurrent_workers,
            "execution_ssh_host": self.execution.ssh.host or None,
            "execution_ssh_sync_enabled": self.execution.ssh.sync_enabled,
            "execution_ssh_sync_mode": self.execution.ssh.sync_mode,
            "multi_agent_checkpoint_enabled": self.multi_agent.checkpoint_enabled,
            "execution_ssh_user": self.execution.ssh.user or None,
            "execution_docker_file_tools": self.execution.docker.file_tools_in_container,
            "openrouter_base_url": self.openrouter_base_url,
            "config_path": str(self.config_path) if self.config_path else None,
            "openrouter_api_key_set": bool(self.openrouter_api_key),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_display_dict(), indent=2)
