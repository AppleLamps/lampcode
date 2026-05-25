from __future__ import annotations

from typing import Any


def parse_oidc_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = str(payload.get("event", payload.get("type", ""))).strip()
    subject = str(payload.get("subject", payload.get("sub", ""))).strip()
    session_id = str(payload.get("session_id", "")).strip() or None
    groups_raw = payload.get("groups", [])
    if isinstance(groups_raw, str):
        groups = [groups_raw]
    elif isinstance(groups_raw, list):
        groups = [str(g) for g in groups_raw]
    else:
        groups = []
    return {
        "event": event,
        "subject": subject,
        "session_id": session_id,
        "groups": groups,
        "raw": payload,
    }
