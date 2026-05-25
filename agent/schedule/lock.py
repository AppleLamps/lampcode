from __future__ import annotations

import json
import os
import time
from pathlib import Path


DEFAULT_LOCK_PATH = Path.home() / ".agent-cli" / "schedule" / "tick.lock"
DEFAULT_TTL_SEC = 120


def _lock_path() -> Path:
    return DEFAULT_LOCK_PATH


def acquire_tick_lock(*, force: bool = False, ttl_sec: int = DEFAULT_TTL_SEC) -> tuple[bool, str]:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if path.is_file() and not force:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            acquired = float(data.get("acquired_at", 0))
            if now - acquired < ttl_sec:
                return False, "lock held"
        except (OSError, json.JSONDecodeError):
            pass
    payload = {"pid": os.getpid(), "acquired_at": now, "ttl_sec": ttl_sec}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return True, "acquired"


def release_tick_lock() -> None:
    path = _lock_path()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def lock_status() -> dict:
    path = _lock_path()
    if not path.is_file():
        return {"held": False}
    try:
        return {"held": True, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return {"held": True}
