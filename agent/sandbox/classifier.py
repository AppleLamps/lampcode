from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class CommandRisk(str, Enum):
    READ = "read"
    TEST = "test"
    WRITE = "write"
    NETWORK = "network"
    RUN = "run"


READ_PATTERNS = [
    re.compile(r"^\s*(cat|type|head|tail|less|more|Get-Content|Get-ChildItem)\b", re.I),
    re.compile(r"^\s*(ls|dir|findstr|select-string|rg|grep|find)\b", re.I),
    re.compile(r"^\s*git\s+(status|diff|log|show|branch)\b", re.I),
    re.compile(r"^\s*(pytest|python\s+-m\s+pytest)\b", re.I),
]

WRITE_PATTERNS = [
    re.compile(r"[>]{1,2}\s*\S"),  # redirects
    re.compile(r"^\s*(rm|del|erase|remove-item)\b", re.I),
    re.compile(r"^\s*(mv|move|move-item|copy|copy-item|cp)\b", re.I),
    re.compile(r"^\s*(mkdir|md|new-item)\b", re.I),
    re.compile(r"^\s*(Set-Content|Out-File|tee)\b", re.I),
    re.compile(r"^\s*echo\s+.+\s+[>]{1,2}\s", re.I),
    re.compile(r"^\s*git\s+(add|commit|checkout|reset|clean|push|merge|rebase)\b", re.I),
    re.compile(r"^\s*(pip|npm|yarn|pnpm)\s+install\b", re.I),
]

NETWORK_PATTERNS = [
    re.compile(r"^\s*(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b", re.I),
    re.compile(r"^\s*(pip|npm|yarn|pnpm)\s+install\b", re.I),
    re.compile(r"^\s*git\s+(push|pull|fetch|clone)\b", re.I),
]


def classify_command(cmd: str) -> CommandRisk:
    cmd = cmd.strip()
    for pat in NETWORK_PATTERNS:
        if pat.search(cmd):
            return CommandRisk.NETWORK
    for pat in WRITE_PATTERNS:
        if pat.search(cmd):
            return CommandRisk.WRITE
    for pat in READ_PATTERNS:
        if pat.search(cmd):
            if re.match(r"^\s*(pytest|python\s+-m\s+pytest)\b", cmd, re.I):
                return CommandRisk.TEST
            return CommandRisk.READ
    if re.match(r"^\s*(pytest|python\s+-m\s+pytest)\b", cmd, re.I):
        return CommandRisk.TEST
    return CommandRisk.RUN


def extract_write_targets(cmd: str) -> list[str]:
    """Best-effort extraction of write target paths from a command."""
    targets: list[str] = []
    for match in re.finditer(r"[>]{1,2}\s*([^\s|;&]+)", cmd):
        targets.append(match.group(1).strip('"').strip("'"))
    for match in re.finditer(
        r"(?:Set-Content|Out-File|tee)\s+(?:-Path\s+)?['\"]?([^\s'\"]+)", cmd, re.I
    ):
        targets.append(match.group(1))
    for match in re.finditer(r"(?:copy|move|cp|mv)\s+\S+\s+([^\s|;&]+)", cmd, re.I):
        targets.append(match.group(1).strip('"').strip("'"))
    return targets


def mcp_tool_is_mutating(tool_name: str) -> bool:
    lower = tool_name.lower()
    keywords = ("write", "create", "delete", "move", "update", "patch", "remove")
    return any(k in lower for k in keywords)


@dataclass
class SandboxDecision:
    allowed: bool
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed
