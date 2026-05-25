from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

from agent.sandbox.classifier import CommandRisk, classify_command


class ExecPolicyMode(str, Enum):
    PROMPT = "prompt"
    UNTRUSTED = "untrusted"
    NEVER = "never"

    @classmethod
    def from_str(cls, value: str | None) -> ExecPolicyMode:
        if not value:
            return cls.PROMPT
        normalized = value.strip().lower()
        for mode in cls:
            if mode.value == normalized:
                return mode
        raise ValueError(f"Invalid exec_policy: {value!r}")


DEFAULT_ALLOW = [
    "git status*",
    "git diff*",
    "pytest*",
    "python -m pytest*",
    "rg *",
    "dir",
    "dir *",
    "ls",
    "ls *",
]

DEFAULT_DENY = [
    "rm -rf *",
    "del /s *",
    "del /s /q *",
    "format *",
]


@dataclass
class ExecPolicyRules:
    allow: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOW))
    deny: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))


@dataclass
class ExecPolicyConfig:
    mode: ExecPolicyMode = ExecPolicyMode.PROMPT
    rules: ExecPolicyRules = field(default_factory=ExecPolicyRules)


def load_exec_policy_config(path: Path | None = None) -> ExecPolicyConfig:
    if tomllib is None:
        return ExecPolicyConfig()
    from agent.paths import default_config_path

    config_path = path or default_config_path()
    if not config_path.is_file():
        return ExecPolicyConfig()
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    mode = ExecPolicyMode.from_str(data.get("exec_policy"))
    rules_raw = data.get("exec_policy_rules", {})
    if not isinstance(rules_raw, dict):
        rules_raw = {}
    allow = list(rules_raw.get("allow", DEFAULT_ALLOW))
    deny = list(rules_raw.get("deny", DEFAULT_DENY))
    return ExecPolicyConfig(
        mode=mode,
        rules=ExecPolicyRules(allow=allow, deny=deny),
    )


def match_rule(cmd: str, pattern: str) -> bool:
    return fnmatch.fnmatch(cmd.strip(), pattern)


def evaluate_command(cmd: str, policy: ExecPolicyConfig) -> dict[str, Any]:
    cmd = cmd.strip()
    if any(match_rule(cmd, p) for p in policy.rules.deny):
        return {"decision": "deny", "reason": "matched deny rule", "auto_approve": False}
    if any(match_rule(cmd, p) for p in policy.rules.allow):
        return {"decision": "allow", "reason": "matched allow rule", "auto_approve": True}
    risk = classify_command(cmd)
    if risk in (CommandRisk.READ, CommandRisk.TEST):
        return {
            "decision": "allow_read",
            "reason": f"classified as {risk.value}",
            "auto_approve": policy.mode == ExecPolicyMode.UNTRUSTED,
        }
    return {"decision": "prompt", "reason": "untrusted command", "auto_approve": False}


def should_prompt_for_command(cmd: str, policy: ExecPolicyConfig) -> bool:
    if policy.mode == ExecPolicyMode.NEVER:
        return False
    if policy.mode == ExecPolicyMode.PROMPT:
        return True
    result = evaluate_command(cmd, policy)
    if result["decision"] == "deny":
        return True
    return not result["auto_approve"]
