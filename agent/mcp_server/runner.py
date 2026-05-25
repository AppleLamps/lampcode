from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.loop import run_turn
from agent.models import AgentMessageItem, Thread, new_id, utc_now_iso
from agent.store import ThreadStore


def run_agent_tool(params: dict[str, Any]) -> dict[str, Any]:
    prompt = str(params.get("prompt", "")).strip()
    if not prompt:
        return {"status": "error", "error": "prompt is required"}

    cwd = params.get("cwd")
    model = params.get("model")
    sandbox_mode = params.get("sandbox_mode")
    auto_approve = bool(params.get("auto_approve", False))
    max_cost = params.get("max_cost")
    profile = params.get("profile")

    try:
        config = Config.resolve(
            cwd=Path(cwd) if cwd else None,
            model=model,
            auto_approve=auto_approve if auto_approve else None,
            sandbox=sandbox_mode,
            max_cost_usd=float(max_cost) if max_cost is not None else None,
            model_profile=profile,
        )
        config.require_api_key()
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    store = ThreadStore.ephemeral()
    thread = Thread(
        id=f"ephemeral-{new_id()}",
        cwd=str(config.cwd),
        model=config.model,
        title="mcp-server run",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )

    turn = run_turn(
        thread,
        prompt,
        config,
        store,
        session_auto_approve=auto_approve,
        headless_json=True,
    )

    final_text = ""
    for item in reversed(turn.items):
        if isinstance(item, AgentMessageItem):
            final_text = item.text
            break

    return {
        "status": turn.status,
        "thread_id": thread.id,
        "turn_id": turn.id,
        "final_text": final_text,
        "cost": turn.usage.estimated_cost_usd,
        "model_used": turn.usage.model_used or config.model,
    }


def format_agent_run_result(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2)
