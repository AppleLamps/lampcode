from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.auth.policy.rules import PolicyRule, ServePolicySettings
from agent.metrics import MetricsCollector


CONTROL_ACTIONS = frozenset(
    {
        "thread.run",
        "thread.approve",
        "thread.cancel",
        "ide.write",
        "sync.resolve",
        "serve.users.add",
        "serve.users.revoke",
    }
)


@dataclass
class PolicyContext:
    role: str
    action: str
    claims: dict[str, Any] = field(default_factory=dict)
    https: bool = True
    session_id: str | None = None
    session_age_sec: float = 0.0
    idle_sec: float = 0.0


@dataclass
class PolicyResult:
    decision: str  # allow | deny | step_up
    message: str = ""
    rule: str = ""


def action_for_route(method: str, path: str) -> str:
    clean = path.split("?")[0].rstrip("/") or "/"
    m = method.upper()
    if m == "PUT" and clean == "/ide/file":
        return "ide.write"
    if m == "POST" and clean.endswith("/run"):
        return "thread.run"
    if m == "POST" and "/approvals/" in clean:
        return "thread.approve"
    if m == "POST" and clean.endswith("/cancel"):
        return "thread.cancel"
    if m == "POST" and clean == "/sync/resolve":
        return "sync.resolve"
    if m == "POST" and "/serve/users" in clean:
        return "serve.users.add"
    if m == "DELETE" and "/serve/users" in clean:
        return "serve.users.revoke"
    if m == "GET" and clean.startswith("/threads"):
        return "thread.read"
    return f"{m.lower()}:{clean}"


def evaluate(context: PolicyContext, settings: ServePolicySettings) -> PolicyResult:
    if not settings.enabled:
        return PolicyResult(decision="allow")

    if settings.require_https and not context.https:
        MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "deny")
        return PolicyResult(decision="deny", message="HTTPS required", rule="require_https")

    if settings.max_session_age_sec > 0 and context.session_age_sec > settings.max_session_age_sec:
        MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "deny")
        return PolicyResult(decision="deny", message="Session max age exceeded", rule="max_session_age")

    if settings.idle_timeout_sec > 0 and context.idle_sec > settings.idle_timeout_sec:
        MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "deny")
        return PolicyResult(decision="deny", message="Session idle timeout", rule="idle_timeout")

    for rule in settings.rules:
        if rule.match_roles and context.role not in rule.match_roles:
            continue
        if context.action in rule.deny_actions:
            MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "deny")
            return PolicyResult(decision="deny", message=rule.deny_message, rule=rule.name)
        for claim_key, expected in rule.require_claims.items():
            actual = context.claims.get(claim_key)
            if isinstance(actual, list):
                ok = expected in actual or str(expected) in [str(x) for x in actual]
            elif claim_key == "amr" and expected == "mfa":
                ok = _has_mfa(context.claims)
            else:
                ok = str(actual) == str(expected)
            if not ok:
                MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "deny")
                return PolicyResult(
                    decision="deny",
                    message=rule.deny_message or f"Missing claim {claim_key}={expected}",
                    rule=rule.name,
                )

    if (
        settings.step_up_for_control_actions
        and context.action in CONTROL_ACTIONS
        and not _has_mfa(context.claims)
        and context.role in ("operator", "admin")
    ):
        for rule in settings.rules:
            if rule.require_claims.get("amr") == "mfa" and context.role in rule.match_roles:
                MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "step_up")
                return PolicyResult(decision="step_up", message="Step-up authentication required", rule="step_up")

    MetricsCollector.global_collector().inc_labeled("agent_auth_policy_total", "allow")
    return PolicyResult(decision="allow")


def _has_mfa(claims: dict[str, Any]) -> bool:
    amr = claims.get("amr", [])
    if isinstance(amr, str):
        amr = [amr]
    if "mfa" in amr or "otp" in amr:
        return True
    if claims.get("acr") in ("mfa", "urn:mace:incommon.iap:silver"):
        return True
    return bool(claims.get("mfa"))


def evaluate_login(
    *,
    role: str,
    claims: dict[str, Any],
    settings: ServePolicySettings,
    https: bool = True,
) -> PolicyResult:
    return evaluate(
        PolicyContext(role=role, action="auth.login", claims=claims, https=https),
        settings,
    )
