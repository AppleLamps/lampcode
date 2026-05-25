from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


def _json_format() -> bool:
    return os.environ.get("AGENT_LOG_FORMAT", "").lower() == "json"


def log_event(
    level: str,
    event: str,
    *,
    thread_id: str | None = None,
    turn_id: str | None = None,
    tool: str | None = None,
    backend: str | None = None,
    message: str | None = None,
    **extra: Any,
) -> None:
    if _json_format():
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
        }
        if thread_id:
            payload["thread_id"] = thread_id
        if turn_id:
            payload["turn_id"] = turn_id
        if tool:
            payload["tool"] = tool
        if backend:
            payload["backend"] = backend
        if message:
            payload["message"] = message
        payload.update(extra)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    elif message:
        print(f"[{level}] {event}: {message}", file=sys.stderr)


def log_info(event: str, **kwargs: Any) -> None:
    log_event("info", event, **kwargs)


def log_warning(event: str, **kwargs: Any) -> None:
    log_event("warning", event, **kwargs)


def log_error(event: str, **kwargs: Any) -> None:
    log_event("error", event, **kwargs)
