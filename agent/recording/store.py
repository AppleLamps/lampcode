from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.events import AgentEvent
from agent.paths import default_runs_dir


@dataclass
class RunRecord:
    thread_id: str
    turn_id: str
    path: Path
    updated_at: float


class RunStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or default_runs_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def run_path(self, thread_id: str, turn_id: str) -> Path:
        thread_dir = self.base_dir / thread_id
        thread_dir.mkdir(parents=True, exist_ok=True)
        return thread_dir / f"{turn_id}.jsonl"

    def append_event(self, thread_id: str, turn_id: str, event: AgentEvent) -> None:
        path = self.run_path(thread_id, turn_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(event.to_json() + "\n")

    def load_events(self, turn_id: str, *, thread_id: str | None = None) -> list[AgentEvent]:
        path = self._resolve_path(turn_id, thread_id=thread_id)
        events: list[AgentEvent] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                events.append(
                    AgentEvent(
                        type=raw["type"],
                        thread_id=raw.get("thread_id"),
                        turn_id=raw.get("turn_id"),
                        data=raw.get("data", {}),
                        timestamp=raw.get("timestamp", ""),
                    )
                )
        return events

    def list_runs(self, thread_id: str | None = None) -> list[RunRecord]:
        records: list[RunRecord] = []
        if thread_id:
            thread_dirs = [self.base_dir / thread_id]
        else:
            thread_dirs = [p for p in self.base_dir.iterdir() if p.is_dir()]

        for thread_dir in thread_dirs:
            if not thread_dir.is_dir():
                continue
            for path in thread_dir.glob("*.jsonl"):
                records.append(
                    RunRecord(
                        thread_id=thread_dir.name,
                        turn_id=path.stem,
                        path=path,
                        updated_at=path.stat().st_mtime,
                    )
                )
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records

    def prune_thread(self, thread_id: str, keep_last: int) -> int:
        runs = [r for r in self.list_runs(thread_id) if r.thread_id == thread_id]
        removed = 0
        for record in runs[keep_last:]:
            record.path.unlink(missing_ok=True)
            removed += 1
        return removed

    def _resolve_path(self, turn_id: str, *, thread_id: str | None = None) -> Path:
        if thread_id:
            path = self.run_path(thread_id, turn_id)
            if path.exists():
                return path

        matches: list[Path] = []
        for thread_dir in self.base_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            candidate = thread_dir / f"{turn_id}.jsonl"
            if candidate.exists():
                matches.append(candidate)
            else:
                for path in thread_dir.glob("*.jsonl"):
                    if path.stem.startswith(turn_id) or turn_id.startswith(path.stem[:8]):
                        matches.append(path)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise FileNotFoundError(f"Ambiguous turn id prefix: {turn_id}")
        raise FileNotFoundError(f"Run log not found: {turn_id}")
