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

from agent.auth.policy.rules import ServePolicySettings


@dataclass
class TelemetrySettings:
    enabled: bool = False
    service_name: str = "agent-cli"
    otlp_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    sample_rate: float = 1.0
    export_console: bool = False
    export_runtime_metrics: bool = True
    histogram_buckets_sec: list[float] = field(
        default_factory=lambda: [0.1, 0.5, 1, 2, 5, 10, 30, 60, 120]
    )


@dataclass
class MarketplaceSettings:
    enabled: bool = False
    allow_unsigned_local: bool = True
    allow_unsigned_cache: bool = False
    require_signature: bool = True
    trusted_publishers: list[str] = field(default_factory=list)
    registry_dir: str = "~/.agent-cli/marketplace"
    remote_registry_url: str = ""
    remote_registry_signature_key: str = ""
    sync_interval_sec: int = 3600
    offline_cache_dir: str = "~/.agent-cli/marketplace/cache"
    revocation_list_url: str = ""
    revocation_list_ttl_sec: int = 900
    pin_versions_in_lockfile: bool = True


@dataclass
class SwarmBudgetPricing:
    input_per_million: float = 0.0
    output_per_million: float = 0.0


@dataclass
class SwarmBudgetSettings:
    enabled: bool = False
    max_wall_clock_sec: int = 3600
    max_supervisor_tool_calls: int = 200
    max_workers_spawned: int = 20
    max_openrouter_input_tokens: int = 500_000
    max_openrouter_output_tokens: int = 200_000
    max_estimated_cost_usd: float = 0.0
    on_budget_exceeded: str = "kill"  # kill | warn
    pricing: dict[str, SwarmBudgetPricing] = field(default_factory=dict)



@dataclass
class SkillsConfig:
    max_active: int = 3
    max_body_chars: int = 4000
    enable_user_skills: bool = True
    enable_project_skills: bool = True
    project_rules_max_chars: int = 8000
    auto_activate: bool = True
    show_active_in_prompt_footer: bool = True
    marketplace: MarketplaceSettings = field(default_factory=MarketplaceSettings)


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
    project_cwd: Path | None = None


@dataclass
class ContextSettings:
    baseline_mode: str = "auto"  # auto | fixed | none
    baseline_tokens: int = 12000
    headroom_tokens: int = 8000
    headroom_pct: float = 0.05
    warn_yellow_left_pct: int = 25
    warn_red_left_pct: int = 10
    artifact_inline_limit: int = 8000
    divergence_threshold: float = 0.15


@dataclass
class CompactionSettings:
    enabled: bool = True
    keep_recent_turns: int = 2
    threshold: float = 0.7
    summary_max_chars: int = 8000
    auto_mid_turn: bool = True
    model: str | None = "google/gemini-2.5-flash-preview"
    pre_turn_threshold: float = 0.85
    tool_output_threshold: float = 0.75


DEFAULT_OPENROUTER_PRICING: dict[str, SwarmBudgetPricing] = {
    "minimax/minimax-m2.7": SwarmBudgetPricing(input_per_million=0.0, output_per_million=0.0),
    "anthropic/claude-sonnet-4": SwarmBudgetPricing(input_per_million=3.0, output_per_million=15.0),
    "anthropic/claude-3.5-sonnet": SwarmBudgetPricing(input_per_million=3.0, output_per_million=15.0),
    "openai/gpt-4.1": SwarmBudgetPricing(input_per_million=2.0, output_per_million=8.0),
    "openai/gpt-4.1-mini": SwarmBudgetPricing(input_per_million=0.4, output_per_million=1.6),
    "google/gemini-2.5-flash-preview": SwarmBudgetPricing(input_per_million=0.15, output_per_million=0.6),
    "google/gemini-2.5-pro-preview": SwarmBudgetPricing(input_per_million=1.25, output_per_million=10.0),
}


@dataclass
class OpenRouterSettings:
    max_retries: int = 3
    retry_base_delay_sec: float = 1.0
    request_timeout_sec: int = 120
    primary_model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    fallback_on: list[str] = field(
        default_factory=lambda: ["rate_limit", "provider_error", "timeout", "context_length"]
    )
    native_fallback: bool = True
    max_tokens: int | None = None
    reasoning_exclude: bool = True
    user_id: str = ""
    require_parameters: bool = False
    app_name: str = "agent-cli"
    app_url: str = "https://github.com/agent-cli"
    pricing: dict[str, SwarmBudgetPricing] = field(default_factory=dict)


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
class ActionLogSettings:
    enabled: bool = True
    dir: str = "~/.agent-cli/logs"
    mirror_to_project: bool = True


@dataclass
class TurnCheckpointSettings:
    enabled: bool = True
    dir: str = "~/.agent-cli/turn-checkpoints"


@dataclass
class ShellSettings:
    enabled: bool = False
    persistent: bool = True
    pty: bool = True
    backend: str = "auto"
    idle_timeout_sec: int = 600
    max_output_chars: int = 20_000
    default_yield_ms: int = 10_000


@dataclass
class HooksSettings:
    fail_on_error: bool = False


@dataclass
class NotifySettings:
    command: str = ""
    on_approval: bool = False
    timeout_sec: int = 10


@dataclass
class MemoriesSettings:
    enabled: bool = False
    max_inject: int = 5
    path: str = "~/.agent-cli/memories.json"
    auto_suggest: bool = False
    suggest_on_auto_approve: bool = True
    suggest_max_pending: int = 20


@dataclass
class PlanModeSettings:
    allowed_tools: list[str] = field(
        default_factory=lambda: [
            "read_file",
            "search_repo",
            "file_outline",
            "go_to_definition",
            "find_references",
            "file_imports",
            "request_user_input",
        ]
    )
    allow_mcp_servers: list[str] = field(default_factory=lambda: ["lsp"])


@dataclass
class BudgetSettings:
    max_cost_usd_per_turn: float | None = None


@dataclass
class HarnessSettings:
    max_parallel_read_tools: int = 4
    post_patch_test: str = ""
    lsp_diagnostics_after_patch: bool = False


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
    api_key_env: str = ""
    api_key: str = ""


DEFAULT_WEB_SEARCH_API_KEY_ENV = {
    "exa": "EXA_API_KEY",
    "tavily": "TAVILY_API_KEY",
}


@dataclass
class DockerExecutionSettings:
    binary: str = "docker"
    platform: str = ""
    file_tools_in_container: bool = False
    read_only_rootfs: bool = True
    user: str = "65532:65532"
    cap_drop_all: bool = True
    security_opt_no_new_privileges: bool = True


@dataclass
class SshJumpSettings:
    host: str = ""
    user: str = ""


@dataclass
class SshSyncSettings:
    mode: str = "incremental"
    transport: str = "auto"
    conflict_strategy: str = "prompt"
    hash_on_conflict: bool = True
    manifest_path: str = ".agent-cli/sync-manifest.json"
    max_files_per_sync: int = 5000
    exclude: list[str] = field(
        default_factory=lambda: [
            ".git/objects",
            "__pycache__",
            ".venv",
            "node_modules",
            ".agent-cli",
        ]
    )
    include_dotfiles: bool = False
    max_upload_mb: int = 200
    checksum: str = "mtime"
    fetch_remote_manifest: bool = True
    remote_scan_max_files: int = 5000
    replicate_remote_state: bool = True
    remote_shell: str = "bash -lc"
    on_remote_manifest_missing: str = "create"


@dataclass
class SshPoolSettings:
    enabled: bool = True
    max_sessions: int = 3
    idle_timeout_sec: int = 300
    healthcheck_cmd: str = "echo ok"


@dataclass
class SshExecutionSettings:
    host: str = ""
    user: str = ""
    port: int = 22
    identity_file: str = ""
    known_hosts: str = ""
    remote_workspace: str = ""
    connect_timeout_sec: int = 15
    command_timeout_sec: int = 120
    strict_host_key_checking: bool = True
    jump: SshJumpSettings = field(default_factory=SshJumpSettings)
    sync_enabled: bool = False
    sync_mode: str = "push"
    sync_on: str = "turn_start"
    pull_on_turn_end: bool = True
    delete_remote_extra: bool = False
    sync: SshSyncSettings = field(default_factory=SshSyncSettings)
    pool: SshPoolSettings = field(default_factory=SshPoolSettings)


@dataclass
class ExecutionSettings:
    backend: str = "local"
    default_image: str = "python:3.12-slim"
    workspace_mount: str = "/workspace"
    network: str = "none"
    memory_limit: str = "1g"
    cpu_limit: str = "1.0"
    command_timeout_sec: int = 120
    auto_background_servers: bool = True
    auto_pull: bool = False
    prefer_hardened_backend: bool = False
    warn_on_unisolated_local: bool = True
    require_interactive_for_danger_full_access: bool = True
    docker: DockerExecutionSettings = field(default_factory=DockerExecutionSettings)
    ssh: SshExecutionSettings = field(default_factory=SshExecutionSettings)
    docker_image_override: str | None = None


@dataclass
class CrossThreadGitSyncSettings:
    repo_path: str = ".agent-cli/program-sync"
    branch: str = "agent-programs"
    remote: str = "origin"
    auto_commit_message: str = "agent-cli program sync"


@dataclass
class CrossThreadS3SyncSettings:
    endpoint_url: str = "https://s3.amazonaws.com"
    bucket: str = ""
    prefix: str = "programs/"
    access_key_env: str = "AGENT_S3_ACCESS_KEY"
    secret_key_env: str = "AGENT_S3_SECRET_KEY"


@dataclass
class CrossThreadSettings:
    enabled: bool = False
    program_id_auto: bool = True
    state_dir: str = "~/.agent-cli/programs"
    max_threads_linked: int = 20
    sync_enabled: bool = False
    sync_backend: str = "git"
    sync_interval_sec: int = 60
    sign_program_state: bool = True
    signing_key_id: str = "program-sync"
    git: CrossThreadGitSyncSettings = field(default_factory=CrossThreadGitSyncSettings)
    s3: CrossThreadS3SyncSettings = field(default_factory=CrossThreadS3SyncSettings)


@dataclass
class MultiAgentSettings:
    enabled: bool = False
    max_workers_per_turn: int = 5
    max_worker_depth: int = 2
    max_concurrent_workers: int = 3
    worker_auto_approve: bool = False
    inherit_execution_backend: bool = True
    wait_timeout_sec: int = 600
    allow_worker_spawn: bool = True
    checkpoint_enabled: bool = True
    checkpoint_dir: str = "~/.agent-cli/checkpoints"
    retry_failed_workers: bool = True
    retry_backoff_sec: int = 5
    retry_max_attempts: int = 2
    checkpoint_compact_after_workers: int = 10
    metrics_enabled: bool = True
    dag_enabled: bool = False
    dag_wall_clock_budget_sec: int = 3600
    dag_fail_fast: bool = False
    dag_persist_across_turns: bool = False
    dag_max_age_sec: int = 86400
    dag_auto_resume: bool = False
    budgets: SwarmBudgetSettings = field(default_factory=SwarmBudgetSettings)
    cross_thread: CrossThreadSettings = field(default_factory=CrossThreadSettings)


@dataclass
class SandboxProfileSettings:
    enabled: bool = False
    profile: str = "auto"
    windows_job_memory_limit_mb: int = 1024
    windows_job_cpu_rate: int = 50
    linux_unshare_user: bool = False
    fail_open: bool = True


@dataclass
class ServeTlsSettings:
    enabled: bool = False
    cert_file: str = "~/.agent-cli/certs/server.crt"
    key_file: str = "~/.agent-cli/certs/server.key"
    auto_generate_self_signed: bool = False
    require_client_cert: bool = False
    client_ca_file: str = "~/.agent-cli/certs/client-ca.pem"


@dataclass
class ServeIdeDiagnosticsSettings:
    enabled: bool = True
    timeout_sec: int = 10
    python_tool: str = "auto"
    js_tool: str = "none"
    max_diagnostics: int = 200
    run_on_open: bool = True


@dataclass
class ServeIdeSettings:
    enabled: bool = False
    max_file_bytes: int = 1_048_576
    max_tree_entries: int = 2000
    max_tree_depth: int = 5
    monaco_cdn: str = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs"
    max_open_tabs: int = 10
    show_diff_gutter: bool = True
    autosave: bool = False
    diagnostics: ServeIdeDiagnosticsSettings = field(default_factory=ServeIdeDiagnosticsSettings)


@dataclass
class RbacUser:
    name: str
    token_hash: str
    role: str = "viewer"


from agent.auth.policy.rules import ServePolicySettings


@dataclass
class ServeWebhookSettings:
    enabled: bool = False
    path: str = "/auth/webhooks/oidc-events"
    shared_secret_env: str = "AGENT_WEBHOOK_SECRET"
    revoke_on_events: list[str] = field(
        default_factory=lambda: ["role_changed", "session_revoked", "password_changed"]
    )
    revoke_all_subject_sessions: bool = True


@dataclass
class ServeRbacSettings:
    enabled: bool = False
    default_role: str = "viewer"
    users: list[RbacUser] = field(default_factory=list)


@dataclass
class ScheduleNotificationSettings:
    enabled: bool = False
    webhook_url: str = ""
    webhook_secret_env: str = "AGENT_SCHEDULE_WEBHOOK_SECRET"
    on_events: list[str] = field(default_factory=lambda: ["failed", "budget_exceeded", "completed"])
    timeout_sec: int = 10
    retry_count: int = 2
    include_transcript_snippet: bool = True
    max_snippet_chars: int = 4000


@dataclass
class ScheduleSettings:
    enabled: bool = False
    require_budgets: bool = True
    require_multi_agent: bool = True
    default_approval_mode: str = "interactive"
    allow_unattended_auto: bool = False
    state_file: str = "~/.agent-cli/schedules.json"
    notifications: ScheduleNotificationSettings = field(default_factory=ScheduleNotificationSettings)


@dataclass
class ServeSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str = ""
    auth_mode: str = "bearer"
    allow_remote_bind: bool = False
    allow_query_tokens: bool = False
    enable_control: bool = True
    enable_turn_start: bool = False
    max_concurrent_turns: int = 2
    approval_timeout_sec: int = 300
    stream_buffer_size: int = 256
    cors: bool = False
    cors_allowed_origins: list[str] = field(default_factory=list)
    max_request_body_bytes: int = 1_048_576
    redact_thread_responses: bool = True
    session_ttl_sec: int = 28800
    session_persist: bool = True
    tls: ServeTlsSettings = field(default_factory=ServeTlsSettings)
    rbac: ServeRbacSettings = field(default_factory=ServeRbacSettings)
    oidc: "ServeOidcSettings | None" = None
    ide: ServeIdeSettings = field(default_factory=ServeIdeSettings)
    policy: ServePolicySettings = field(default_factory=ServePolicySettings)
    webhooks: ServeWebhookSettings = field(default_factory=ServeWebhookSettings)


def _load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_context_settings(path: Path | None = None) -> ContextSettings:
    data = _load_toml(path or default_config_path())
    ctx = data.get("context", {})
    if not isinstance(ctx, dict):
        ctx = {}
    return ContextSettings(
        baseline_mode=str(ctx.get("baseline_mode", "auto")),
        baseline_tokens=int(ctx.get("baseline_tokens", 12000)),
        headroom_tokens=int(ctx.get("headroom_tokens", 8000)),
        headroom_pct=float(ctx.get("headroom_pct", 0.05)),
        warn_yellow_left_pct=int(ctx.get("warn_left_pct", ctx.get("warn_yellow_left_pct", 25))),
        warn_red_left_pct=int(ctx.get("warn_red_left_pct", 10)),
        artifact_inline_limit=int(ctx.get("artifact_inline_limit", 8000)),
        divergence_threshold=float(ctx.get("divergence_threshold", 0.15)),
    )


def load_compaction_settings(path: Path | None = None) -> CompactionSettings:
    data = _load_toml(path or default_config_path())
    compaction = data.get("compaction", {})
    if not isinstance(compaction, dict):
        compaction = {}
    model = compaction.get("model")
    return CompactionSettings(
        enabled=bool(compaction.get("enabled", True)),
        keep_recent_turns=int(compaction.get("keep_recent_turns", 2)),
        threshold=float(compaction.get("threshold", data.get("compaction_threshold", 0.7))),
        summary_max_chars=int(compaction.get("summary_max_chars", 8000)),
        auto_mid_turn=bool(compaction.get("auto_mid_turn", True)),
        model=str(model) if model else "google/gemini-2.5-flash-preview",
        pre_turn_threshold=float(compaction.get("pre_turn_threshold", 0.85)),
        tool_output_threshold=float(compaction.get("tool_output_threshold", 0.75)),
    )


def _parse_openrouter_pricing(raw: Any) -> dict[str, SwarmBudgetPricing]:
    pricing: dict[str, SwarmBudgetPricing] = {}
    if not isinstance(raw, dict):
        return pricing
    for model, p in raw.items():
        if isinstance(p, dict):
            pricing[str(model)] = SwarmBudgetPricing(
                input_per_million=float(p.get("input", p.get("input_per_million", 0))),
                output_per_million=float(p.get("output", p.get("output_per_million", 0))),
            )
    return pricing


def _parse_openrouter_section(or_cfg: dict[str, Any]) -> OpenRouterSettings:
    fb = or_cfg.get("fallback_models", [])
    fo = or_cfg.get("fallback_on", ["rate_limit", "provider_error", "timeout", "context_length"])
    max_tokens_raw = or_cfg.get("max_tokens")
    max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None
    return OpenRouterSettings(
        max_retries=int(or_cfg.get("max_retries", 3)),
        retry_base_delay_sec=float(or_cfg.get("retry_base_delay_sec", 1.0)),
        request_timeout_sec=int(or_cfg.get("request_timeout_sec", 120)),
        primary_model=str(or_cfg.get("primary_model", "")),
        fallback_models=[str(x) for x in fb] if isinstance(fb, list) else [],
        fallback_on=[str(x) for x in fo] if isinstance(fo, list) else ["rate_limit", "provider_error", "timeout", "context_length"],
        native_fallback=bool(or_cfg.get("native_fallback", True)),
        max_tokens=max_tokens,
        reasoning_exclude=bool(or_cfg.get("reasoning_exclude", True)),
        user_id=str(or_cfg.get("user_id", "")),
        require_parameters=bool(or_cfg.get("require_parameters", False)),
        app_name=str(or_cfg.get("app_name", "agent-cli")),
        app_url=str(or_cfg.get("app_url", "https://github.com/agent-cli")),
        pricing=_parse_openrouter_pricing(or_cfg.get("pricing", {})),
    )


def load_openrouter_settings(path: Path | None = None, *, project_path: Path | None = None) -> OpenRouterSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_or = user_data.get("openrouter", {})
    project_or = project_data.get("openrouter", {})
    if not isinstance(user_or, dict):
        user_or = {}
    if not isinstance(project_or, dict):
        project_or = {}
    merged = {**user_or, **project_or}
    fb_user = user_or.get("fallback_models", [])
    fb_project = project_or.get("fallback_models", [])
    if isinstance(fb_user, list) or isinstance(fb_project, list):
        combined: list[str] = []
        seen: set[str] = set()
        for src in (fb_user if isinstance(fb_user, list) else [], fb_project if isinstance(fb_project, list) else []):
            for m in src:
                s = str(m)
                if s not in seen:
                    seen.add(s)
                    combined.append(s)
        merged["fallback_models"] = combined
    user_pricing = _parse_openrouter_pricing(user_or.get("pricing", {}))
    project_pricing = _parse_openrouter_pricing(project_or.get("pricing", {}))
    pricing = {**DEFAULT_OPENROUTER_PRICING, **user_pricing, **project_pricing}
    merged["pricing"] = {
        k: {"input_per_million": v.input_per_million, "output_per_million": v.output_per_million}
        for k, v in pricing.items()
    }
    return _parse_openrouter_section(merged)


def load_action_log_settings(path: Path | None = None) -> ActionLogSettings:
    data = _load_toml(path or default_config_path())
    section = data.get("action_log", {})
    if not isinstance(section, dict):
        section = {}
    return ActionLogSettings(
        enabled=bool(section.get("enabled", True)),
        dir=str(section.get("dir", "~/.agent-cli/logs")),
        mirror_to_project=bool(section.get("mirror_to_project", True)),
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


def load_turn_checkpoint_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> TurnCheckpointSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_tc = user_data.get("turn_checkpoint", {})
    project_tc = project_data.get("turn_checkpoint", {})
    if not isinstance(user_tc, dict):
        user_tc = {}
    if not isinstance(project_tc, dict):
        project_tc = {}
    merged = {**user_tc, **project_tc}
    return TurnCheckpointSettings(
        enabled=bool(merged.get("enabled", True)),
        dir=str(merged.get("dir", "~/.agent-cli/turn-checkpoints")),
    )


def load_shell_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> ShellSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_sh = user_data.get("shell", {})
    project_sh = project_data.get("shell", {})
    if not isinstance(user_sh, dict):
        user_sh = {}
    if not isinstance(project_sh, dict):
        project_sh = {}
    merged = {**user_sh, **project_sh}
    return ShellSettings(
        enabled=bool(merged.get("enabled", False)),
        persistent=bool(merged.get("persistent", True)),
        pty=bool(merged.get("pty", True)),
        backend=str(merged.get("backend", "auto")),
        idle_timeout_sec=int(merged.get("idle_timeout_sec", 600)),
        max_output_chars=int(merged.get("max_output_chars", 20_000)),
        default_yield_ms=int(merged.get("default_yield_ms", 10_000)),
    )


def load_harness_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> HarnessSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_h = user_data.get("harness", {})
    project_h = project_data.get("harness", {})
    if not isinstance(user_h, dict):
        user_h = {}
    if not isinstance(project_h, dict):
        project_h = {}
    merged = {**user_h, **project_h}
    return HarnessSettings(
        max_parallel_read_tools=int(merged.get("max_parallel_read_tools", 4)),
        post_patch_test=str(merged.get("post_patch_test", "")),
        lsp_diagnostics_after_patch=bool(merged.get("lsp_diagnostics_after_patch", False)),
    )


def load_budget_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> BudgetSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_b = user_data.get("budget", {})
    project_b = project_data.get("budget", {})
    if not isinstance(user_b, dict):
        user_b = {}
    if not isinstance(project_b, dict):
        project_b = {}
    merged = {**user_b, **project_b}
    max_cost = merged.get("max_cost_usd_per_turn")
    return BudgetSettings(
        max_cost_usd_per_turn=float(max_cost) if max_cost is not None else None,
    )


def load_hooks_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> HooksSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_h = user_data.get("hooks", {})
    project_h = project_data.get("hooks", {})
    if not isinstance(user_h, dict):
        user_h = {}
    if not isinstance(project_h, dict):
        project_h = {}
    merged = {**user_h, **project_h}
    return HooksSettings(fail_on_error=bool(merged.get("fail_on_error", False)))


def load_memories_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> MemoriesSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_m = user_data.get("memories", {})
    project_m = project_data.get("memories", {})
    if not isinstance(user_m, dict):
        user_m = {}
    if not isinstance(project_m, dict):
        project_m = {}
    merged = {**user_m, **project_m}
    return MemoriesSettings(
        enabled=bool(merged.get("enabled", False)),
        max_inject=int(merged.get("max_inject", 5)),
        path=str(merged.get("path", "~/.agent-cli/memories.json")),
        auto_suggest=bool(merged.get("auto_suggest", False)),
        suggest_on_auto_approve=bool(merged.get("suggest_on_auto_approve", True)),
        suggest_max_pending=int(merged.get("suggest_max_pending", 20)),
    )


def load_notify_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> NotifySettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_n = user_data.get("notify", {})
    project_n = project_data.get("notify", {})
    if not isinstance(user_n, dict):
        user_n = {}
    if not isinstance(project_n, dict):
        project_n = {}
    merged = {**user_n, **project_n}
    return NotifySettings(
        command=str(merged.get("command", "")),
        on_approval=bool(merged.get("on_approval", False)),
        timeout_sec=int(merged.get("timeout_sec", 10)),
    )


def load_plan_mode_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> PlanModeSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_p = user_data.get("plan_mode", {})
    project_p = project_data.get("plan_mode", {})
    if not isinstance(user_p, dict):
        user_p = {}
    if not isinstance(project_p, dict):
        project_p = {}
    merged = {**user_p, **project_p}
    defaults = PlanModeSettings()
    allowed = merged.get("allowed_tools", defaults.allowed_tools)
    if not isinstance(allowed, list):
        allowed = defaults.allowed_tools
    allow_mcp = merged.get("allow_mcp_servers", defaults.allow_mcp_servers)
    if not isinstance(allow_mcp, list):
        allow_mcp = defaults.allow_mcp_servers
    return PlanModeSettings(
        allowed_tools=[str(t) for t in allowed],
        allow_mcp_servers=[str(s) for s in allow_mcp],
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


def load_web_search_settings(
    path: Path | None = None,
    *,
    project_path: Path | None = None,
) -> WebSearchSettings:
    user_data = _load_toml(path or default_config_path())
    project_data = _load_toml(project_path) if project_path else {}
    user_ws = user_data.get("web_search", {})
    project_ws = project_data.get("web_search", {})
    if not isinstance(user_ws, dict):
        user_ws = {}
    if not isinstance(project_ws, dict):
        project_ws = {}
    merged = {**user_ws, **project_ws}
    provider = str(merged.get("provider", "duckduckgo"))
    api_key_env = str(merged.get("api_key_env", ""))
    if not api_key_env:
        api_key_env = DEFAULT_WEB_SEARCH_API_KEY_ENV.get(provider, "")
    return WebSearchSettings(
        enabled=bool(merged.get("enabled", False)),
        provider=provider,
        max_results=int(merged.get("max_results", 5)),
        timeout_sec=int(merged.get("timeout_sec", 15)),
        api_key_env=api_key_env,
        api_key=str(merged.get("api_key", "")),
    )


def load_execution_settings(path: Path | None = None) -> ExecutionSettings:
    data = _load_toml(path or default_config_path())
    exe = data.get("execution", {})
    if not isinstance(exe, dict):
        exe = {}
    docker_raw = exe.get("docker", {})
    if not isinstance(docker_raw, dict):
        docker_raw = {}
    ssh_raw = exe.get("ssh", {})
    if not isinstance(ssh_raw, dict):
        ssh_raw = {}
    jump_raw = ssh_raw.get("jump", {})
    if not isinstance(jump_raw, dict):
        jump_raw = {}
    sync_raw = ssh_raw.get("sync", {})
    if not isinstance(sync_raw, dict):
        sync_raw = {}
    pool_raw = ssh_raw.get("pool", {})
    if not isinstance(pool_raw, dict):
        pool_raw = {}
    exclude = sync_raw.get("exclude", [
        ".git/objects", "__pycache__", ".venv", "node_modules", ".agent-cli",
    ])
    if not isinstance(exclude, list):
        exclude = list(SshSyncSettings().exclude)
    return ExecutionSettings(
        backend=str(exe.get("backend", "local")),
        default_image=str(exe.get("default_image", "python:3.12-slim")),
        workspace_mount=str(exe.get("workspace_mount", "/workspace")),
        network=str(exe.get("network", "none")),
        memory_limit=str(exe.get("memory_limit", "1g")),
        cpu_limit=str(exe.get("cpu_limit", "1.0")),
        command_timeout_sec=int(exe.get("command_timeout_sec", 120)),
        auto_background_servers=bool(exe.get("auto_background_servers", True)),
        auto_pull=bool(exe.get("auto_pull", False)),
        prefer_hardened_backend=bool(exe.get("prefer_hardened_backend", False)),
        warn_on_unisolated_local=bool(exe.get("warn_on_unisolated_local", True)),
        require_interactive_for_danger_full_access=bool(
            exe.get("require_interactive_for_danger_full_access", True)
        ),
        docker=DockerExecutionSettings(
            binary=str(docker_raw.get("binary", "docker")),
            platform=str(docker_raw.get("platform", "")),
            file_tools_in_container=bool(docker_raw.get("file_tools_in_container", False)),
            read_only_rootfs=bool(docker_raw.get("read_only_rootfs", True)),
            user=str(docker_raw.get("user", "65532:65532")),
            cap_drop_all=bool(docker_raw.get("cap_drop_all", True)),
            security_opt_no_new_privileges=bool(
                docker_raw.get("security_opt_no_new_privileges", True)
            ),
        ),
        ssh=SshExecutionSettings(
            host=str(ssh_raw.get("host", "")),
            user=str(ssh_raw.get("user", "")),
            port=int(ssh_raw.get("port", 22)),
            identity_file=str(ssh_raw.get("identity_file", "")),
            known_hosts=str(ssh_raw.get("known_hosts", "")),
            remote_workspace=str(ssh_raw.get("remote_workspace", "")),
            connect_timeout_sec=int(ssh_raw.get("connect_timeout_sec", 15)),
            command_timeout_sec=int(ssh_raw.get("command_timeout_sec", 120)),
            strict_host_key_checking=bool(ssh_raw.get("strict_host_key_checking", True)),
            jump=SshJumpSettings(
                host=str(jump_raw.get("host", "")),
                user=str(jump_raw.get("user", "")),
            ),
            sync_enabled=bool(ssh_raw.get("sync_enabled", False)),
            sync_mode=str(ssh_raw.get("sync_mode", "push")),
            sync_on=str(ssh_raw.get("sync_on", "turn_start")),
            pull_on_turn_end=bool(ssh_raw.get("pull_on_turn_end", True)),
            delete_remote_extra=bool(ssh_raw.get("delete_remote_extra", False)),
            sync=SshSyncSettings(
                mode=str(sync_raw.get("mode", "incremental")),
                transport=str(sync_raw.get("transport", "auto")),
                conflict_strategy=str(sync_raw.get("conflict_strategy", "prompt")),
                hash_on_conflict=bool(sync_raw.get("hash_on_conflict", True)),
                manifest_path=str(sync_raw.get("manifest_path", ".agent-cli/sync-manifest.json")),
                max_files_per_sync=int(sync_raw.get("max_files_per_sync", 5000)),
                exclude=[str(x) for x in exclude],
                include_dotfiles=bool(sync_raw.get("include_dotfiles", False)),
                max_upload_mb=int(sync_raw.get("max_upload_mb", 200)),
                checksum=str(sync_raw.get("checksum", "mtime")),
                fetch_remote_manifest=bool(sync_raw.get("fetch_remote_manifest", True)),
                remote_scan_max_files=int(sync_raw.get("remote_scan_max_files", 5000)),
                replicate_remote_state=bool(sync_raw.get("replicate_remote_state", True)),
                remote_shell=str(sync_raw.get("remote_shell", "bash -lc")),
                on_remote_manifest_missing=str(sync_raw.get("on_remote_manifest_missing", "create")),
            ),
            pool=SshPoolSettings(
                enabled=bool(pool_raw.get("enabled", True)),
                max_sessions=int(pool_raw.get("max_sessions", 3)),
                idle_timeout_sec=int(pool_raw.get("idle_timeout_sec", 300)),
                healthcheck_cmd=str(pool_raw.get("healthcheck_cmd", "echo ok")),
            ),
        ),
    )


def load_multi_agent_settings(path: Path | None = None) -> MultiAgentSettings:
    data = _load_toml(path or default_config_path())
    ma = data.get("multi_agent", {})
    if not isinstance(ma, dict):
        ma = {}
    ct = ma.get("cross_thread", {})
    if not isinstance(ct, dict):
        ct = {}
    git_raw = ct.get("git", {})
    if not isinstance(git_raw, dict):
        git_raw = {}
    s3_raw = ct.get("s3", {})
    if not isinstance(s3_raw, dict):
        s3_raw = {}
    return MultiAgentSettings(
        enabled=bool(ma.get("enabled", False)),
        max_workers_per_turn=int(ma.get("max_workers_per_turn", 5)),
        max_worker_depth=int(ma.get("max_worker_depth", 2)),
        max_concurrent_workers=int(ma.get("max_concurrent_workers", 3)),
        worker_auto_approve=bool(ma.get("worker_auto_approve", False)),
        inherit_execution_backend=bool(ma.get("inherit_execution_backend", True)),
        wait_timeout_sec=int(ma.get("wait_timeout_sec", 600)),
        allow_worker_spawn=bool(ma.get("allow_worker_spawn", True)),
        checkpoint_enabled=bool(ma.get("checkpoint_enabled", True)),
        checkpoint_dir=str(ma.get("checkpoint_dir", "~/.agent-cli/checkpoints")),
        retry_failed_workers=bool(ma.get("retry_failed_workers", True)),
        retry_backoff_sec=int(ma.get("retry_backoff_sec", 5)),
        retry_max_attempts=int(ma.get("retry_max_attempts", 2)),
        checkpoint_compact_after_workers=int(ma.get("checkpoint_compact_after_workers", 10)),
        metrics_enabled=bool(ma.get("metrics_enabled", True)),
        dag_enabled=bool(ma.get("dag_enabled", False)),
        dag_wall_clock_budget_sec=int(ma.get("dag_wall_clock_budget_sec", 3600)),
        dag_fail_fast=bool(ma.get("dag_fail_fast", False)),
        dag_persist_across_turns=bool(ma.get("dag_persist_across_turns", False)),
        dag_max_age_sec=int(ma.get("dag_max_age_sec", 86400)),
        dag_auto_resume=bool(ma.get("dag_auto_resume", False)),
        budgets=_parse_swarm_budgets(ma.get("budgets", {})),
        cross_thread=CrossThreadSettings(
            enabled=bool(ct.get("enabled", False)),
            program_id_auto=bool(ct.get("program_id_auto", True)),
            state_dir=str(ct.get("state_dir", "~/.agent-cli/programs")),
            max_threads_linked=int(ct.get("max_threads_linked", 20)),
            sync_enabled=bool(ct.get("sync_enabled", False)),
            sync_backend=str(ct.get("sync_backend", "git")),
            sync_interval_sec=int(ct.get("sync_interval_sec", 60)),
            sign_program_state=bool(ct.get("sign_program_state", True)),
            signing_key_id=str(ct.get("signing_key_id", "program-sync")),
            git=CrossThreadGitSyncSettings(
                repo_path=str(git_raw.get("repo_path", ".agent-cli/program-sync")),
                branch=str(git_raw.get("branch", "agent-programs")),
                remote=str(git_raw.get("remote", "origin")),
                auto_commit_message=str(git_raw.get("auto_commit_message", "agent-cli program sync")),
            ),
            s3=CrossThreadS3SyncSettings(
                endpoint_url=str(s3_raw.get("endpoint_url", "https://s3.amazonaws.com")),
                bucket=str(s3_raw.get("bucket", "")),
                prefix=str(s3_raw.get("prefix", "programs/")),
                access_key_env=str(s3_raw.get("access_key_env", "AGENT_S3_ACCESS_KEY")),
                secret_key_env=str(s3_raw.get("secret_key_env", "AGENT_S3_SECRET_KEY")),
            ),
        ),
    )


def _parse_swarm_budgets(raw: Any) -> SwarmBudgetSettings:
    if not isinstance(raw, dict):
        return SwarmBudgetSettings()
    pricing_raw = raw.get("pricing", {})
    pricing: dict[str, SwarmBudgetPricing] = {}
    if isinstance(pricing_raw, dict):
        for model, p in pricing_raw.items():
            if isinstance(p, dict):
                pricing[str(model)] = SwarmBudgetPricing(
                    input_per_million=float(p.get("input", p.get("input_per_million", 0))),
                    output_per_million=float(p.get("output", p.get("output_per_million", 0))),
                )
    return SwarmBudgetSettings(
        enabled=bool(raw.get("enabled", False)),
        max_wall_clock_sec=int(raw.get("max_wall_clock_sec", 3600)),
        max_supervisor_tool_calls=int(raw.get("max_supervisor_tool_calls", 200)),
        max_workers_spawned=int(raw.get("max_workers_spawned", 20)),
        max_openrouter_input_tokens=int(raw.get("max_openrouter_input_tokens", 500_000)),
        max_openrouter_output_tokens=int(raw.get("max_openrouter_output_tokens", 200_000)),
        max_estimated_cost_usd=float(raw.get("max_estimated_cost_usd", 0)),
        on_budget_exceeded=str(raw.get("on_budget_exceeded", "kill")),
        pricing=pricing,
    )


def load_telemetry_settings(path: Path | None = None) -> TelemetrySettings:
    data = _load_toml(path or default_config_path())
    tel = data.get("telemetry", {})
    if not isinstance(tel, dict):
        tel = {}
    return TelemetrySettings(
        enabled=bool(tel.get("enabled", False)),
        service_name=str(tel.get("service_name", "agent-cli")),
        otlp_endpoint=str(tel.get("otlp_endpoint", "http://127.0.0.1:4318/v1/traces")),
        sample_rate=float(tel.get("sample_rate", 1.0)),
        export_console=bool(tel.get("export_console", False)),
        export_runtime_metrics=bool(tel.get("export_runtime_metrics", True)),
        histogram_buckets_sec=[
            float(x) for x in tel.get("histogram_buckets_sec", [0.1, 0.5, 1, 2, 5, 10, 30, 60, 120])
        ],
    )


def load_sandbox_profile_settings(path: Path | None = None) -> SandboxProfileSettings:
    data = _load_toml(path or default_config_path())
    sp = data.get("sandbox_profiles", {})
    if not isinstance(sp, dict):
        sp = {}
    return SandboxProfileSettings(
        enabled=bool(sp.get("enabled", False)),
        profile=str(sp.get("profile", "auto")),
        windows_job_memory_limit_mb=int(sp.get("windows_job_memory_limit_mb", 1024)),
        windows_job_cpu_rate=int(sp.get("windows_job_cpu_rate", 50)),
        linux_unshare_user=bool(sp.get("linux_unshare_user", False)),
        fail_open=bool(sp.get("fail_open", True)),
    )


def load_kernel_sandbox_settings(path: Path | None = None):
    from agent.sandbox.kernel import (
        KernelLinuxSettings,
        KernelMacosSettings,
        KernelSandboxSettings,
        KernelWindowsSettings,
    )

    data = _load_toml(path or default_config_path())
    sk = data.get("sandbox", {}).get("kernel", data.get("sandbox_kernel", {}))
    if not isinstance(sk, dict):
        sk = {}
    linux_raw = sk.get("linux", {})
    if not isinstance(linux_raw, dict):
        linux_raw = {}
    mac_raw = sk.get("macos", {})
    if not isinstance(mac_raw, dict):
        mac_raw = {}
    win_raw = sk.get("windows", {})
    if not isinstance(win_raw, dict):
        win_raw = {}
    ro = linux_raw.get("ro_bind_paths", ["/usr", "/lib", "/bin"])
    if not isinstance(ro, list):
        ro = ["/usr", "/lib", "/bin"]
    apply_to = sk.get("apply_to", ["run_command"])
    if not isinstance(apply_to, list):
        apply_to = ["run_command"]
    return KernelSandboxSettings(
        enabled=bool(sk.get("enabled", False)),
        backend=str(sk.get("backend", "auto")),
        fail_open=bool(sk.get("fail_open", True)),
        apply_to=[str(x) for x in apply_to],
        linux=KernelLinuxSettings(
            bwrap_binary=str(linux_raw.get("bwrap_binary", "bwrap")),
            unshare_user=bool(linux_raw.get("unshare_user", False)),
            ro_bind_paths=[str(x) for x in ro],
            allow_network=bool(linux_raw.get("allow_network", False)),
        ),
        macos=KernelMacosSettings(
            sandbox_exec=str(mac_raw.get("sandbox_exec", "/usr/bin/sandbox-exec")),
            profile_template=str(mac_raw.get("profile_template", "workspace-write")),
        ),
        windows=KernelWindowsSettings(
            use_restricted_token=bool(win_raw.get("use_restricted_token", True)),
            job_object_memory_mb=int(win_raw.get("job_object_memory_mb", 1024)),
            job_object_cpu_rate=int(win_raw.get("job_object_cpu_rate", 50)),
            allow_network=bool(win_raw.get("allow_network", False)),
            backend_preference=str(
                win_raw.get("backend_preference", "appcontainer_then_restricted")
            ),
            capability_sids=[str(x) for x in win_raw.get("capability_sids", [])]
            if isinstance(win_raw.get("capability_sids"), list)
            else [],
            workspace_cap=bool(win_raw.get("workspace_cap", True)),
        ),
    )


def load_serve_settings(path: Path | None = None) -> ServeSettings:
    data = _load_toml(path or default_config_path())
    serve = data.get("serve", {})
    if not isinstance(serve, dict):
        serve = {}
    tls_raw = serve.get("tls", {})
    if not isinstance(tls_raw, dict):
        tls_raw = {}
    rbac_raw = serve.get("rbac", {})
    if not isinstance(rbac_raw, dict):
        rbac_raw = {}
    users_raw = rbac_raw.get("users", serve.get("rbac_users", []))
    if not isinstance(users_raw, list):
        users_raw = []
    users: list[RbacUser] = []
    for u in users_raw:
        if isinstance(u, dict) and u.get("name"):
            users.append(
                RbacUser(
                    name=str(u["name"]),
                    token_hash=str(u.get("token_hash", "")),
                    role=str(u.get("role", "viewer")),
                )
            )
    auth_raw = serve.get("auth", {})
    if not isinstance(auth_raw, dict):
        auth_raw = {}
    auth_mode = str(auth_raw.get("mode", serve.get("auth_mode", "bearer")))
    session_ttl = int(auth_raw.get("session_ttl_sec", serve.get("session_ttl_sec", 28800)))
    oidc_cfg = None
    oidc_raw = auth_raw.get("oidc", serve.get("oidc", {}))
    if isinstance(oidc_raw, dict) and oidc_raw.get("issuer_url"):
        from agent.serve.oidc import OidcRoleMapping, ServeOidcSettings

        rm_raw = oidc_raw.get("role_mapping", {})
        if not isinstance(rm_raw, dict):
            rm_raw = {}
        admin_g = rm_raw.get("admin_groups", [])
        op_g = rm_raw.get("operator_groups", [])
        scopes = oidc_raw.get("scopes", ["openid", "profile", "email"])
        oidc_cfg = ServeOidcSettings(
            enabled=True,
            issuer_url=str(oidc_raw.get("issuer_url", "")),
            client_id=str(oidc_raw.get("client_id", "")),
            client_secret=str(oidc_raw.get("client_secret", "")),
            redirect_uri=str(oidc_raw.get("redirect_uri", "https://127.0.0.1:8765/auth/oidc/callback")),
            scopes=[str(s) for s in scopes] if isinstance(scopes, list) else ["openid", "profile", "email"],
            pkce=bool(oidc_raw.get("pkce", True)),
            session_ttl_sec=int(oidc_raw.get("session_ttl_sec", session_ttl)),
            role_mapping=OidcRoleMapping(
                admin_groups=[str(x) for x in admin_g] if isinstance(admin_g, list) else [],
                operator_groups=[str(x) for x in op_g] if isinstance(op_g, list) else [],
                default_role=str(rm_raw.get("default_role", "viewer")),
                claim_groups_key=str(rm_raw.get("claim_groups_key", "groups")),
                claim_email_key=str(rm_raw.get("claim_email_key", "email")),
            ),
            device_code_enabled=bool(oidc_raw.get("device_code_enabled", False)),
            device_client_id=str(oidc_raw.get("device_client_id", "")),
            refresh_rotation=bool(oidc_raw.get("refresh_rotation", True)),
            refresh_skew_sec=int(oidc_raw.get("refresh_skew_sec", 300)),
            prefer_keyring=bool(oidc_raw.get("prefer_keyring", True)),
        )
    ide_raw = serve.get("ide", {})
    if not isinstance(ide_raw, dict):
        ide_raw = {}
    diag_raw = ide_raw.get("diagnostics", {})
    if not isinstance(diag_raw, dict):
        diag_raw = {}
    from agent.auth.policy.rules import load_policy_settings

    policy = load_policy_settings(auth_raw if isinstance(auth_raw, dict) else {})
    webhooks_raw = auth_raw.get("webhooks", {}) if isinstance(auth_raw, dict) else {}
    if not isinstance(webhooks_raw, dict):
        webhooks_raw = {}
    revoke_events = webhooks_raw.get("revoke_on_events", ["role_changed", "session_revoked", "password_changed"])
    if not isinstance(revoke_events, list):
        revoke_events = ["role_changed", "session_revoked", "password_changed"]
    webhooks = ServeWebhookSettings(
        enabled=bool(webhooks_raw.get("enabled", False)),
        path=str(webhooks_raw.get("path", "/auth/webhooks/oidc-events")),
        shared_secret_env=str(webhooks_raw.get("shared_secret_env", "AGENT_WEBHOOK_SECRET")),
        revoke_on_events=[str(x) for x in revoke_events],
        revoke_all_subject_sessions=bool(webhooks_raw.get("revoke_all_subject_sessions", True)),
    )
    cors_origins = serve.get("cors_allowed_origins", serve.get("allowed_origins", []))
    if not isinstance(cors_origins, list):
        cors_origins = []
    return ServeSettings(
        host=str(serve.get("host", "127.0.0.1")),
        port=int(serve.get("port", 8765)),
        auth_token=str(serve.get("auth_token", auth_raw.get("legacy_token", ""))),
        auth_mode=auth_mode,
        allow_remote_bind=bool(serve.get("allow_remote_bind", False)),
        allow_query_tokens=bool(serve.get("allow_query_tokens", auth_raw.get("allow_query_tokens", False))),
        enable_control=bool(serve.get("enable_control", True)),
        enable_turn_start=bool(serve.get("enable_turn_start", False)),
        max_concurrent_turns=int(serve.get("max_concurrent_turns", 2)),
        approval_timeout_sec=int(serve.get("approval_timeout_sec", 300)),
        stream_buffer_size=int(serve.get("stream_buffer_size", 256)),
        cors=bool(serve.get("cors", False)),
        cors_allowed_origins=[str(x) for x in cors_origins],
        max_request_body_bytes=int(serve.get("max_request_body_bytes", 1_048_576)),
        redact_thread_responses=bool(serve.get("redact_thread_responses", True)),
        session_ttl_sec=session_ttl,
        session_persist=bool(serve.get("session_persist", True)),
        tls=ServeTlsSettings(
            enabled=bool(tls_raw.get("enabled", False)),
            cert_file=str(tls_raw.get("cert_file", "~/.agent-cli/certs/server.crt")),
            key_file=str(tls_raw.get("key_file", "~/.agent-cli/certs/server.key")),
            auto_generate_self_signed=bool(tls_raw.get("auto_generate_self_signed", False)),
            require_client_cert=bool(tls_raw.get("require_client_cert", False)),
            client_ca_file=str(tls_raw.get("client_ca_file", "~/.agent-cli/certs/client-ca.pem")),
        ),
        rbac=ServeRbacSettings(
            enabled=bool(rbac_raw.get("enabled", False)),
            default_role=str(rbac_raw.get("default_role", "viewer")),
            users=users,
        ),
        oidc=oidc_cfg,
        ide=ServeIdeSettings(
            enabled=bool(ide_raw.get("enabled", False)),
            max_file_bytes=int(ide_raw.get("max_file_bytes", 1_048_576)),
            max_tree_entries=int(ide_raw.get("max_tree_entries", 2000)),
            max_tree_depth=int(ide_raw.get("max_tree_depth", 5)),
            monaco_cdn=str(
                ide_raw.get(
                    "monaco_cdn",
                    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs",
                )
            ),
            max_open_tabs=int(ide_raw.get("max_open_tabs", 10)),
            show_diff_gutter=bool(ide_raw.get("show_diff_gutter", True)),
            autosave=bool(ide_raw.get("autosave", False)),
            diagnostics=ServeIdeDiagnosticsSettings(
                enabled=bool(diag_raw.get("enabled", True)),
                timeout_sec=int(diag_raw.get("timeout_sec", 10)),
                python_tool=str(diag_raw.get("python_tool", "auto")),
                js_tool=str(diag_raw.get("js_tool", "none")),
                max_diagnostics=int(diag_raw.get("max_diagnostics", 200)),
                run_on_open=bool(diag_raw.get("run_on_open", True)),
            ),
        ),
        policy=policy,
        webhooks=webhooks,
    )


def load_schedule_settings(path: Path | None = None) -> ScheduleSettings:
    data = _load_toml(path or default_config_path())
    sched = data.get("schedule", {})
    if not isinstance(sched, dict):
        sched = {}
    notif_raw = sched.get("notifications", {})
    if not isinstance(notif_raw, dict):
        notif_raw = {}
    on_events = notif_raw.get("on_events", ["failed", "budget_exceeded", "completed"])
    if not isinstance(on_events, list):
        on_events = ["failed", "budget_exceeded", "completed"]
    return ScheduleSettings(
        enabled=bool(sched.get("enabled", False)),
        require_budgets=bool(sched.get("require_budgets", True)),
        require_multi_agent=bool(sched.get("require_multi_agent", True)),
        default_approval_mode=str(sched.get("default_approval_mode", "interactive")),
        allow_unattended_auto=bool(sched.get("allow_unattended_auto", False)),
        state_file=str(sched.get("state_file", "~/.agent-cli/schedules.json")),
        notifications=ScheduleNotificationSettings(
            enabled=bool(notif_raw.get("enabled", False)),
            webhook_url=str(notif_raw.get("webhook_url", "")),
            webhook_secret_env=str(notif_raw.get("webhook_secret_env", "AGENT_SCHEDULE_WEBHOOK_SECRET")),
            on_events=[str(x) for x in on_events],
            timeout_sec=int(notif_raw.get("timeout_sec", 10)),
            retry_count=int(notif_raw.get("retry_count", 2)),
            include_transcript_snippet=bool(notif_raw.get("include_transcript_snippet", True)),
            max_snippet_chars=int(notif_raw.get("max_snippet_chars", 4000)),
        ),
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
    mp_raw = skills.get("marketplace", {})
    if not isinstance(mp_raw, dict):
        mp_raw = {}
    trusted = mp_raw.get("trusted_publishers", [])
    if not isinstance(trusted, list):
        trusted = []
    return SkillsConfig(
        max_active=int(skills.get("max_active", 3)),
        max_body_chars=int(skills.get("max_body_chars", 4000)),
        enable_user_skills=bool(skills.get("enable_user_skills", True)),
        enable_project_skills=bool(skills.get("enable_project_skills", True)),
        project_rules_max_chars=int(skills.get("project_rules_max_chars", 8000)),
        auto_activate=bool(skills.get("auto_activate", True)),
        show_active_in_prompt_footer=bool(skills.get("show_active_in_prompt_footer", True)),
        marketplace=MarketplaceSettings(
            enabled=bool(mp_raw.get("enabled", False)),
            allow_unsigned_local=bool(mp_raw.get("allow_unsigned_local", True)),
            allow_unsigned_cache=bool(mp_raw.get("allow_unsigned_cache", False)),
            require_signature=bool(mp_raw.get("require_signature", True)),
            trusted_publishers=[str(x) for x in trusted],
            registry_dir=str(mp_raw.get("registry_dir", "~/.agent-cli/marketplace")),
            remote_registry_url=str(mp_raw.get("remote_registry_url", "")),
            remote_registry_signature_key=str(mp_raw.get("remote_registry_signature_key", "")),
            sync_interval_sec=int(mp_raw.get("sync_interval_sec", 3600)),
            offline_cache_dir=str(mp_raw.get("offline_cache_dir", "~/.agent-cli/marketplace/cache")),
            revocation_list_url=str(mp_raw.get("revocation_list_url", "")),
            revocation_list_ttl_sec=int(mp_raw.get("revocation_list_ttl_sec", 900)),
            pin_versions_in_lockfile=bool(mp_raw.get("pin_versions_in_lockfile", True)),
        ),
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
        project_cwd=project_cwd.resolve() if project_cwd else None,
    )


def load_auth_storage_settings(path: Path | None = None):
    from agent.auth.storage import AuthStorageSettings

    data = _load_toml(path or default_config_path())
    auth = data.get("auth", {})
    if not isinstance(auth, dict):
        auth = {}
    storage = auth.get("storage", {})
    if not isinstance(storage, dict):
        storage = {}
    return AuthStorageSettings(
        backend=str(storage.get("backend", "auto")),
        service_name=str(storage.get("service_name", "agent-cli")),
        file_path=str(storage.get("file_path", "")),
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
