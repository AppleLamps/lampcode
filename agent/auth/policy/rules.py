from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicyRule:
    name: str
    match_roles: list[str] = field(default_factory=list)
    deny_actions: list[str] = field(default_factory=list)
    require_claims: dict[str, str] = field(default_factory=dict)
    deny_message: str = "Policy denied"


@dataclass
class ServePolicySettings:
    enabled: bool = False
    require_https: bool = True
    introspection_url: str = ""
    introspection_client_id: str = ""
    introspection_client_secret: str = ""
    revoke_on_role_change: bool = True
    max_session_age_sec: int = 28800
    idle_timeout_sec: int = 3600
    step_up_for_control_actions: bool = True
    rules: list[PolicyRule] = field(default_factory=list)


def parse_policy_rules(raw: list[Any]) -> list[PolicyRule]:
    rules: list[PolicyRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rc = item.get("require_claims", {})
        if not isinstance(rc, dict):
            rc = {}
        rules.append(
            PolicyRule(
                name=str(item.get("name", "rule")),
                match_roles=[str(r) for r in item.get("match_roles", [])],
                deny_actions=[str(a) for a in item.get("deny_actions", [])],
                require_claims={str(k): str(v) for k, v in rc.items()},
                deny_message=str(item.get("deny_message", "Policy denied")),
            )
        )
    return rules


def load_policy_settings(data: dict[str, Any]) -> ServePolicySettings:
    auth = data.get("auth", {})
    if not isinstance(auth, dict):
        auth = {}
    policy_raw = auth.get("policy", {})
    if not isinstance(policy_raw, dict):
        policy_raw = {}
    rules_raw = policy_raw.get("rules", [])
    if not isinstance(rules_raw, list):
        rules_raw = []
    return ServePolicySettings(
        enabled=bool(policy_raw.get("enabled", False)),
        require_https=bool(policy_raw.get("require_https", True)),
        introspection_url=str(policy_raw.get("introspection_url", "")),
        introspection_client_id=str(policy_raw.get("introspection_client_id", "")),
        introspection_client_secret=str(policy_raw.get("introspection_client_secret", "")),
        revoke_on_role_change=bool(policy_raw.get("revoke_on_role_change", True)),
        max_session_age_sec=int(policy_raw.get("max_session_age_sec", 28800)),
        idle_timeout_sec=int(policy_raw.get("idle_timeout_sec", 3600)),
        step_up_for_control_actions=bool(policy_raw.get("step_up_for_control_actions", True)),
        rules=parse_policy_rules(rules_raw),
    )
