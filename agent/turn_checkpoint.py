from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.models import utc_now_iso


def default_turn_checkpoint_dir() -> Path:
    return Path.home() / ".agent-cli" / "turn-checkpoints"


@dataclass
class TurnCheckpointSettings:
    enabled: bool = True
    dir: str = "~/.agent-cli/turn-checkpoints"


@dataclass
class TurnCheckpoint:
    thread_id: str
    turn_id: str
    user_text: str
    messages: list[Any] = field(default_factory=list)
    status: str = "cancelled"
    saved_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "user_text": self.user_text,
            "messages": self.messages,
            "status": self.status,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnCheckpoint:
        return cls(
            thread_id=str(data["thread_id"]),
            turn_id=str(data["turn_id"]),
            user_text=str(data.get("user_text", "")),
            messages=list(data.get("messages") or []),
            status=str(data.get("status", "cancelled")),
            saved_at=str(data.get("saved_at", utc_now_iso())),
        )


class TurnCheckpointStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or default_turn_checkpoint_dir()

    def _path(self, thread_id: str, turn_id: str) -> Path:
        return self.base_dir / thread_id / f"{turn_id}.json"

    def save(self, checkpoint: TurnCheckpoint) -> Path:
        path = self._path(checkpoint.thread_id, checkpoint.turn_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.saved_at = checkpoint.saved_at or utc_now_iso()
        path.write_text(json.dumps(checkpoint.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, thread_id: str, turn_id: str) -> TurnCheckpoint | None:
        path = self._path(thread_id, turn_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return TurnCheckpoint.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None
        return None

    def find_latest(self, thread_id: str) -> TurnCheckpoint | None:
        thread_dir = self.base_dir / thread_id
        if not thread_dir.is_dir():
            return None
        candidates: list[tuple[float, TurnCheckpoint]] = []
        for path in thread_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cp = TurnCheckpoint.from_dict(data)
                    ts = datetime.fromisoformat(cp.saved_at.replace("Z", "+00:00")).timestamp()
                    candidates.append((ts, cp))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def delete(self, thread_id: str, turn_id: str) -> None:
        path = self._path(thread_id, turn_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        thread_dir = self.base_dir / thread_id
        try:
            if thread_dir.is_dir() and not any(thread_dir.iterdir()):
                thread_dir.rmdir()
        except OSError:
            pass

    def list_for_thread(self, thread_id: str) -> list[TurnCheckpoint]:
        thread_dir = self.base_dir / thread_id
        if not thread_dir.is_dir():
            return []
        out: list[TurnCheckpoint] = []
        for path in sorted(thread_dir.glob("*.json")):
            cp = self.load(thread_id, path.stem)
            if cp:
                out.append(cp)
        return out


def save_turn_checkpoint(
    *,
    settings: TurnCheckpointSettings,
    thread_id: str,
    turn_id: str,
    user_text: str,
    messages: list[Any],
    status: str = "cancelled",
) -> Path | None:
    if not settings.enabled:
        return None
    store = TurnCheckpointStore(Path(settings.dir).expanduser())
    return store.save(
        TurnCheckpoint(
            thread_id=thread_id,
            turn_id=turn_id,
            user_text=user_text,
            messages=messages,
            status=status,
        )
    )


def clear_turn_checkpoint(settings: TurnCheckpointSettings, thread_id: str, turn_id: str) -> None:
    if not settings.enabled:
        return
    TurnCheckpointStore(Path(settings.dir).expanduser()).delete(thread_id, turn_id)
