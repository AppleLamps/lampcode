from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScheduleJob:
    id: str
    enabled: bool = True
    cron: str = "0 * * * *"
    cwd: str = "."
    prompt: str = ""
    multi_agent: bool = True
    budget_profile: str = "strict"
    require_approval: bool = True
    max_runtime_sec: int = 1800
    notify_on: list[str] = field(default_factory=lambda: ["failed", "budget_exceeded"])
    program_id: str = ""
    last_run_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleJob:
        return cls(
            id=str(data.get("id", "")),
            enabled=bool(data.get("enabled", True)),
            cron=str(data.get("cron", "0 * * * *")),
            cwd=str(data.get("cwd", ".")),
            prompt=str(data.get("prompt", "")),
            multi_agent=bool(data.get("multi_agent", True)),
            budget_profile=str(data.get("budget_profile", "strict")),
            require_approval=bool(data.get("require_approval", True)),
            max_runtime_sec=int(data.get("max_runtime_sec", 1800)),
            notify_on=list(data.get("notify_on", ["failed", "budget_exceeded"])),
            program_id=str(data.get("program_id", "")),
            last_run_at=float(data.get("last_run_at", 0)),
        )


class ScheduleStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".agent-cli" / "schedules.json"
        self._jobs: dict[str, ScheduleJob] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in data.get("jobs", []):
                job = ScheduleJob.from_dict(item)
                if job.id:
                    self._jobs[job.id] = job
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"jobs": [j.to_dict() for j in self._jobs.values()]}, indent=2),
            encoding="utf-8",
        )

    def list_jobs(self) -> list[ScheduleJob]:
        return list(self._jobs.values())

    def get(self, job_id: str) -> ScheduleJob | None:
        return self._jobs.get(job_id)

    def add(self, job: ScheduleJob) -> ScheduleJob:
        if not job.id:
            job.id = str(uuid.uuid4())[:12]
        self._jobs[job.id] = job
        self.save()
        return job

    def remove(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            self.save()
            return True
        return False

    def update_last_run(self, job_id: str, ts: float | None = None) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.last_run_at = ts or time.time()
            self.save()


def history_dir(base: Path | None = None) -> Path:
    return base or Path.home() / ".agent-cli" / "schedule-runs"


def write_run_history(job_id: str, payload: dict[str, Any], base: Path | None = None) -> Path:
    root = history_dir(base) / job_id
    root.mkdir(parents=True, exist_ok=True)
    ts = int(payload.get("started_at", time.time()))
    path = root / f"{ts}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def list_run_history(job_id: str | None = None, base: Path | None = None) -> list[dict[str, Any]]:
    root = history_dir(base)
    if not root.is_dir():
        return []
    paths: list[Path] = []
    if job_id:
        paths = sorted((root / job_id).glob("*.json")) if (root / job_id).is_dir() else []
    else:
        paths = sorted(root.glob("*/*.json"))
    out: list[dict[str, Any]] = []
    for p in paths[-50:]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["_path"] = str(p)
            out.append(data)
        except (OSError, json.JSONDecodeError):
            pass
    return out
