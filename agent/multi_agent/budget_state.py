from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def budget_state_dir() -> Path:
    return Path.home() / ".agent-cli" / "budgets"


def save_budget_state(thread_id: str, snapshot: dict[str, Any]) -> Path:
    base = budget_state_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{thread_id}.json"
    payload = {"thread_id": thread_id, "updated_at": time.time(), "snapshot": snapshot}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_budget_state(thread_id: str) -> dict[str, Any] | None:
    path = budget_state_dir() / f"{thread_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("snapshot", data))
    except (json.JSONDecodeError, TypeError):
        return None
