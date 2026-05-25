from __future__ import annotations

import fnmatch
import re
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
    allow_prefixes: list[str] = field(default_factory=list)


def project_exec_policy_path(cwd: Path) -> Path:
    return cwd / ".agent-cli" / "exec-policy.toml"


def load_allow_prefixes(cwd: Path) -> list[str]:
    if tomllib is None:
        return []
    path = project_exec_policy_path(cwd)
    if not path.is_file():
        return []
    with path.open("rb") as f:
        data = tomllib.load(f)
    section = data.get("allow_prefixes", {})
    if not isinstance(section, dict):
        return []
    raw = section.get("prefixes", [])
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if str(p).strip()]


def append_allow_prefix(cwd: Path, prefix: str) -> None:
    if tomllib is None:
        raise RuntimeError("tomllib unavailable")
    normalized = " ".join(prefix.strip().split())
    if not normalized:
        return
    path = project_exec_policy_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_allow_prefixes(cwd)
    if normalized in existing:
        return
    existing.append(normalized)
    lines = ["[allow_prefixes]", "prefixes = ["]
    for item in existing:
        lines.append(f'    "{item}",')
    lines.append("]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def command_prefix_tokens(cmd: str, *, count: int = 2) -> str:
    tokens = re.split(r"\s+", cmd.strip())
    return " ".join(tokens[:count]) if tokens else cmd.strip()


def matches_allow_prefix(cmd: str, prefixes: list[str]) -> bool:
    cmd = cmd.strip()
    if not cmd or not prefixes:
        return False
    for prefix in prefixes:
        normalized = prefix.strip()
        if not normalized:
            continue
        if cmd == normalized or cmd.startswith(f"{normalized} "):
            return True
    return False


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
    if matches_allow_prefix(cmd, policy.allow_prefixes):
        return {
            "decision": "allow",
            "reason": "matched allow_prefix",
            "auto_approve": True,
        }
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
        result = evaluate_command(cmd, policy)
        if result["decision"] == "deny":
            return True
        if result["auto_approve"]:
            return False
        return True
    result = evaluate_command(cmd, policy)
    if result["decision"] == "deny":
        return True
    return not result["auto_approve"]
