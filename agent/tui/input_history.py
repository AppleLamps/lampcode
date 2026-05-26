"""Cross-session composer input history and reverse search."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InputHistory:
    path: Path
    max_entries: int = 200
    _entries: list[str] = field(default_factory=list)

    def load(self) -> None:
        if not self.path.is_file():
            self._entries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._entries = [str(x) for x in data if str(x).strip()][-self.max_entries :]
            else:
                self._entries = []
        except (OSError, json.JSONDecodeError):
            self._entries = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries[-self.max_entries :], indent=0), encoding="utf-8")

    def add(self, text: str) -> None:
        stripped = text.strip()
        if not stripped or stripped.startswith("/"):
            return
        if self._entries and self._entries[-1] == stripped:
            return
        self._entries.append(stripped)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        self.save()

    def all_entries(self) -> list[str]:
        return list(self._entries)

    def reverse_search(self, query: str) -> str | None:
        q = query.lower()
        for entry in reversed(self._entries):
            if q in entry.lower():
                return entry
        return None

    def history_path(cwd: Path) -> Path:
        return cwd.resolve() / ".agent-cli" / "composer-history.json"
