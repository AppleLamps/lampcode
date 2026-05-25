from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

from agent.profiles import project_config_path
from agent.paths import default_config_path


@dataclass
class ModelRoutingRule:
    match: str
    profile: str


@dataclass
class ModelRoutingSettings:
    enabled: bool = True
    rules: list[ModelRoutingRule] = field(default_factory=list)


DEFAULT_ROUTING_RULES: list[ModelRoutingRule] = [
    ModelRoutingRule(match=r"fix test|pytest|failing test", profile="deep"),
    ModelRoutingRule(match=r"summarize|explain", profile="fast"),
]


def _load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data if isinstance(data, dict) else {}


def _parse_rules(raw: Any) -> list[ModelRoutingRule]:
    if not isinstance(raw, list):
        return []
    rules: list[ModelRoutingRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match", "")).strip()
        profile = str(item.get("profile", "")).strip()
        if match and profile:
            rules.append(ModelRoutingRule(match=match, profile=profile))
    return rules


def load_model_routing(
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
) -> ModelRoutingSettings:
    user_path = user_path or default_config_path()
    user_data = _load_toml(user_path)
    project_data = _load_toml(project_path) if project_path else {}
    user_mr = user_data.get("model_routing", {})
    project_mr = project_data.get("model_routing", {})
    if not isinstance(user_mr, dict):
        user_mr = {}
    if not isinstance(project_mr, dict):
        project_mr = {}

    enabled = bool(project_mr.get("enabled", user_mr.get("enabled", True)))
    rules = _parse_rules(project_mr.get("rules"))
    if not rules:
        rules = _parse_rules(user_mr.get("rules"))
    if not rules:
        rules = list(DEFAULT_ROUTING_RULES)
    return ModelRoutingSettings(enabled=enabled, rules=rules)


def resolve_model_profile_from_task(
    task: str,
    *,
    cli_model_profile: str | None = None,
    cwd: Path | None = None,
) -> str | None:
    """Return model profile name from routing rules, or None if CLI flag wins / no match."""
    if cli_model_profile:
        return cli_model_profile
    project_path = project_config_path(cwd) if cwd else None
    routing = load_model_routing(project_path=project_path)
    if not routing.enabled:
        return None
    task_l = task.lower()
    for rule in routing.rules:
        if re.search(rule.match, task_l, re.IGNORECASE):
            return rule.profile
    return None
