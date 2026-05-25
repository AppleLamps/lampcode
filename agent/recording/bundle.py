from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.events import AgentEvent
from agent.json_stream import normalize_event
from agent.recording.store import RunStore
from agent.store import ThreadStore

BUNDLE_SCHEMA_VERSION = "v1"
MANIFEST_NAME = "manifest.json"
THREAD_NAME = "thread.jsonl"
EVENTS_NAME = "events.jsonl"
CONFIG_NAME = "config.redacted.json"


def _agent_version() -> str:
    try:
        from importlib.metadata import version

        return version("agent-cli")
    except Exception:
        return "unknown"


def read_bundle_manifest(bundle_path: Path) -> dict[str, Any]:
    with tarfile.open(bundle_path, "r:gz") as tar:
        member = tar.getmember(MANIFEST_NAME)
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"Missing {MANIFEST_NAME} in bundle")
        return json.loads(extracted.read().decode("utf-8"))


def export_run_bundle(
    *,
    turn_id: str,
    thread_id: str | None,
    out_path: Path,
    config: Config | None = None,
    run_store: RunStore | None = None,
    thread_store: ThreadStore | None = None,
) -> dict[str, Any]:
    run_store = run_store or RunStore()
    thread_store = thread_store or ThreadStore()
    events = run_store.load_events(turn_id, thread_id=thread_id)
    if not events:
        raise FileNotFoundError(f"No events for turn {turn_id}")

    resolved_thread_id = thread_id or events[0].thread_id
    if not resolved_thread_id:
        for ev in events:
            if ev.thread_id:
                resolved_thread_id = ev.thread_id
                break
    if not resolved_thread_id:
        raise ValueError("Could not resolve thread_id from run events")

    resolved_turn_id = events[0].turn_id or turn_id
    thread_jsonl = ""
    if thread_store.thread_path(resolved_thread_id).is_file():
        thread_jsonl = thread_store.thread_path(resolved_thread_id).read_text(encoding="utf-8")

    normalized_lines = [normalize_event(ev).to_json() for ev in events]
    events_jsonl = "\n".join(normalized_lines)

    cfg = config or Config.resolve(cwd=Path.cwd())
    config_json = json.dumps(cfg.to_redacted_dict(), indent=2)

    model = cfg.model
    cost = _extract_cost(events)
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "agent_version": _agent_version(),
        "thread_id": resolved_thread_id,
        "turn_id": resolved_turn_id,
        "model": model,
        "cost": cost,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_path, "w:gz") as tar:
        _add_text(tar, MANIFEST_NAME, json.dumps(manifest, indent=2))
        _add_text(tar, THREAD_NAME, thread_jsonl)
        _add_text(tar, EVENTS_NAME, events_jsonl)
        _add_text(tar, CONFIG_NAME, config_json)

    return manifest


def _add_text(tar: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _extract_cost(events: list[AgentEvent]) -> float | None:
    for ev in reversed(events):
        if ev.type in ("turn.completed", "run.summary"):
            cost = ev.data.get("cost") or ev.data.get("estimated_cost_usd")
            if cost is not None:
                return float(cost)
    return None
