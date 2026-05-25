from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.metrics import MetricsCollector
from agent.multi_agent.program_state import ProgramState, ProgramStore
from agent.programs.sync.git_backend import GitProgramBackend
from agent.programs.sync.merge import MergeResult, merge_program_states
from agent.programs.sync.s3_backend import S3ProgramBackend
from agent.programs.sync.signing import sign_state, verify_signed_payload
from agent.settings import CrossThreadSettings


@dataclass
class SyncStatus:
    enabled: bool
    backend: str
    last_pull_at: float = 0.0
    last_push_at: float = 0.0
    program_ids: list[str] = field(default_factory=list)


def create_backend(config: Config, *, cwd: Path | None = None):
    ct = config.multi_agent.cross_thread
    backend = (ct.sync_backend or "none").lower()
    if backend == "git":
        return GitProgramBackend(ct.git, cwd=cwd or config.cwd)
    if backend == "s3":
        return S3ProgramBackend(ct.s3)
    return None


class ProgramSyncCoordinator:
    def __init__(
        self,
        config: Config,
        store: ProgramStore | None = None,
        *,
        backend: Any = None,
        emitter: Any = None,
        cwd: Path | None = None,
    ) -> None:
        self.config = config
        ct = config.multi_agent.cross_thread
        self.settings: CrossThreadSettings = ct
        self.store = store or ProgramStore(Path(ct.state_dir).expanduser())
        self.backend = backend if backend is not None else create_backend(config, cwd=cwd)
        self.emitter = emitter
        self._last_pull: dict[str, float] = {}
        self._last_push: dict[str, float] = {}
        self._meta_path = Path(ct.state_dir).expanduser() / ".sync-meta.json"
        self._load_meta()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.sync_enabled and self.backend is not None)

    def _load_meta(self) -> None:
        if not self._meta_path.is_file():
            return
        try:
            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
            self._last_pull = {k: float(v) for k, v in data.get("last_pull", {}).items()}
            self._last_push = {k: float(v) for k, v in data.get("last_push", {}).items()}
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _save_meta(self) -> None:
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        self._meta_path.write_text(
            json.dumps({"last_pull": self._last_pull, "last_push": self._last_push}, indent=2),
            encoding="utf-8",
        )

    def _encode_payload(self, state: ProgramState) -> bytes:
        data = state.to_dict()
        if self.settings.sign_program_state:
            wrapped = sign_state(data, key_id=self.settings.signing_key_id)
        else:
            wrapped = {"state": data}
        return json.dumps(wrapped, indent=2).encode("utf-8")

    def _decode_payload(self, raw: bytes) -> ProgramState:
        payload = json.loads(raw.decode("utf-8"))
        if self.settings.sign_program_state:
            if not verify_signed_payload(payload):
                raise ValueError("Program signature verification failed")
        state_dict = payload.get("state", payload)
        return ProgramState.from_dict(state_dict)

    def push(self, program_id: str) -> dict[str, Any]:
        if not self.enabled or not self.backend:
            return {"ok": False, "error": "sync disabled"}
        state = self.store.load(program_id)
        if not state:
            return {"ok": False, "error": "program not found"}
        try:
            blob = self._encode_payload(state)
            self.backend.push(program_id, blob)
            self._last_push[program_id] = time.time()
            self._save_meta()
            MetricsCollector.global_collector().inc_labeled("agent_program_sync_total", f"{self.backend.name}:push_ok")
            return {"ok": True, "program_id": program_id, "backend": self.backend.name}
        except Exception as exc:
            MetricsCollector.global_collector().inc_labeled("agent_program_sync_total", f"{self.backend.name}:push_error")
            return {"ok": False, "error": str(exc)}

    def pull(self, program_id: str) -> dict[str, Any]:
        if not self.enabled or not self.backend:
            return {"ok": False, "error": "sync disabled"}
        try:
            raw = self.backend.pull(program_id)
            if raw is None:
                MetricsCollector.global_collector().inc_labeled("agent_program_sync_total", f"{self.backend.name}:pull_miss")
                return {"ok": True, "program_id": program_id, "updated": False}
            remote = self._decode_payload(raw)
            local = self.store.load(program_id)
            last_sync = self._last_pull.get(program_id, 0.0)
            result: MergeResult = merge_program_states(local, remote, last_sync_at=last_sync)
            self.store.save(result.state)
            self._last_pull[program_id] = time.time()
            self._save_meta()
            if result.conflicts and self.emitter:
                self.emitter.multi_agent_program_sync_conflict(
                    program_id=program_id,
                    node_ids=result.conflicts,
                )
            MetricsCollector.global_collector().inc_labeled(
                "agent_program_sync_total", f"{self.backend.name}:pull_ok"
            )
            return {
                "ok": True,
                "program_id": program_id,
                "updated": result.merged,
                "conflicts": result.conflicts,
            }
        except Exception as exc:
            MetricsCollector.global_collector().inc_labeled("agent_program_sync_total", f"{self.backend.name}:pull_error")
            return {"ok": False, "error": str(exc)}

    def pull_if_stale(self, program_id: str) -> None:
        if not self.enabled:
            return
        last = self._last_pull.get(program_id, 0.0)
        if time.time() - last < self.settings.sync_interval_sec:
            return
        self.pull(program_id)

    def schedule_push(self, program_id: str) -> None:
        if not self.enabled:
            return
        last = self._last_push.get(program_id, 0.0)
        if time.time() - last < max(5, self.settings.sync_interval_sec // 2):
            return
        self.push(program_id)

    def verify_signature(self, program_id: str) -> dict[str, Any]:
        state = self.store.load(program_id)
        if not state:
            return {"ok": False, "error": "not found"}
        if not self.settings.sign_program_state:
            return {"ok": True, "signed": False, "program_id": program_id}
        try:
            wrapped = sign_state(state.to_dict(), key_id=self.settings.signing_key_id)
            ok = verify_signed_payload(wrapped)
            return {"ok": ok, "signed": True, "program_id": program_id}
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc), "program_id": program_id}

    def status(self) -> SyncStatus:
        return SyncStatus(
            enabled=self.enabled,
            backend=self.backend.name if self.backend else "none",
            last_pull_at=max(self._last_pull.values()) if self._last_pull else 0.0,
            last_push_at=max(self._last_push.values()) if self._last_push else 0.0,
            program_ids=self.store.list_programs(),
        )


_coordinator: ProgramSyncCoordinator | None = None


def get_coordinator(config: Config | None = None) -> ProgramSyncCoordinator | None:
    global _coordinator
    if config is not None:
        ct = config.multi_agent.cross_thread
        if ct.sync_enabled:
            _coordinator = ProgramSyncCoordinator(config)
        else:
            _coordinator = None
    return _coordinator


def reset_coordinator_for_tests() -> None:
    global _coordinator
    _coordinator = None
