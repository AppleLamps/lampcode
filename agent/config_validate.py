from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.config import Config
from agent.settings import load_serve_settings


@dataclass
class ValidationIssue:
    level: str  # error | warning
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    def exit_code(self, *, strict: bool = False) -> int:
        if self.errors:
            return 1
        if strict and self.warnings:
            return 1
        if self.warnings:
            return 2
        return 0


def validate_config(config: Config | None = None, *, config_path: Path | None = None) -> ValidationResult:
    result = ValidationResult()
    cfg = config or Config.resolve(config_path=config_path)
    serve = load_serve_settings(cfg.config_path)

    if cfg.execution.backend == "ssh":
        ssh = cfg.execution.ssh
        if not ssh.host:
            result.issues.append(ValidationIssue("error", "execution.ssh.host required when backend=ssh"))
        if not ssh.user:
            result.issues.append(ValidationIssue("error", "execution.ssh.user required when backend=ssh"))
        if not ssh.remote_workspace:
            result.issues.append(
                ValidationIssue("error", "execution.ssh.remote_workspace required when backend=ssh")
            )

    if cfg.exec_policy.mode.value == "never" and cfg.approval_mode == "interactive":
        result.issues.append(
            ValidationIssue(
                "warning",
                "exec_policy=never with approval_mode=interactive — approvals will never prompt",
            )
        )

    if serve.allow_remote_bind and not serve.auth_token:
        result.issues.append(
            ValidationIssue(
                "warning",
                "serve.allow_remote_bind=true without auth_token — bind only with a token set",
            )
        )

    if (
        cfg.execution.ssh.sync_enabled
        and cfg.execution.ssh.sync_mode == "push-pull"
        and cfg.execution.ssh.sync.conflict_strategy == "remote-wins"
    ):
        result.issues.append(
            ValidationIssue(
                "warning",
                "sync_mode=push-pull with conflict_strategy=remote-wins may overwrite local work on pull",
            )
        )

    if serve.enable_turn_start and not serve.auth_token and not serve.rbac.enabled:
        result.issues.append(
            ValidationIssue(
                "warning",
                "serve.enable_turn_start=true without auth_token — token will be auto-generated at startup",
            )
        )

    if (
        serve.host in ("0.0.0.0", "::")
        and not serve.tls.enabled
        and not serve.rbac.enabled
    ):
        result.issues.append(
            ValidationIssue(
                "error",
                "Refusing 0.0.0.0 bind without TLS and RBAC — enable serve.tls or serve.rbac",
            )
        )

    if serve.rbac.enabled and not serve.rbac.users and not serve.auth_token:
        from agent.serve.users import load_dynamic_users

        if not load_dynamic_users():
            result.issues.append(
                ValidationIssue(
                    "error",
                    "serve.rbac.enabled=true but no users configured — add [[serve.rbac.users]] or agent serve users add",
                )
            )

    if serve.tls.auto_generate_self_signed:
        result.issues.append(
            ValidationIssue(
                "warning",
                "serve.tls.auto_generate_self_signed=true — dev only; use proper certs in production",
            )
        )

    if serve.tls.enabled:
        from agent.serve.tls import expand_path

        cert = expand_path(serve.tls.cert_file)
        key = expand_path(serve.tls.key_file)
        if not cert.is_file():
            result.issues.append(ValidationIssue("error", f"TLS cert missing: {cert}"))
        if not key.is_file():
            result.issues.append(ValidationIssue("error", f"TLS key missing: {key}"))
        if serve.tls.require_client_cert:
            from agent.serve.tls import expand_path, validate_client_ca

            ca = expand_path(serve.tls.client_ca_file)
            ok, msg = validate_client_ca(ca)
            if not ok:
                result.issues.append(ValidationIssue("error", f"mTLS client CA invalid: {msg}"))

    if serve.ide.enabled and not serve.rbac.enabled:
        result.issues.append(
            ValidationIssue(
                "warning",
                "IDE enabled without RBAC — all authenticated users get admin-equivalent access when RBAC off",
            )
        )

    if serve.oidc and serve.oidc.enabled:
        if not serve.oidc.issuer_url or not serve.oidc.client_id:
            result.issues.append(
                ValidationIssue("error", "OIDC enabled but issuer_url or client_id missing")
            )
        if not serve.tls.enabled:
            result.issues.append(
                ValidationIssue(
                    "error",
                    "OIDC requires TLS enabled (serve.tls.enabled=true)",
                )
            )
        if not serve.rbac.enabled:
            result.issues.append(
                ValidationIssue(
                    "warning",
                    "OIDC enabled without RBAC — role mapping still applies but local RBAC table unused",
                )
            )

    if cfg.sandbox_kernel.enabled:
        from agent.sandbox.kernel.doctor import probe_capabilities

        cap = probe_capabilities(cfg.sandbox_kernel)
        if not cap.get("available"):
            result.issues.append(
                ValidationIssue(
                    "warning",
                    f"Kernel sandbox enabled but {cap.get('backend')} unavailable — will fail_open={cfg.sandbox_kernel.fail_open}",
                )
            )

    import os

    if not os.environ.get("OPENROUTER_API_KEY") and not getattr(cfg, "openrouter_api_key", None):
        result.issues.append(
            ValidationIssue("warning", "OPENROUTER_API_KEY not set — agent run will fail until configured")
        )

    return result
