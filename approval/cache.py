from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, default=str)


def approval_cache_keys(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name == "apply_patch":
        patch = arguments.get("patch", "")
        files: list[str] = []
        for line in patch.splitlines():
            line = line.strip()
            for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
                if line.startswith(prefix):
                    path = line.split(":", 1)[1].strip()
                    files.append(f"apply_patch:{path}")
        if files:
            return files
    digest = hashlib.sha256(f"{tool_name}:{_stable_json(arguments)}".encode()).hexdigest()[:16]
    return [f"{tool_name}:{digest}"]


@dataclass
class ApprovalCache:
    """Session-scoped approval decisions keyed by tool + normalized args."""

    _approved: set[str] = field(default_factory=set)

    def is_approved(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return any(key in self._approved for key in approval_cache_keys(tool_name, arguments))

    def record(self, tool_name: str, arguments: dict[str, Any]) -> None:
        for key in approval_cache_keys(tool_name, arguments):
            self._approved.add(key)

    def clear(self) -> None:
        self._approved.clear()
