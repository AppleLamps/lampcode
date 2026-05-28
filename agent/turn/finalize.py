"""Turn cancellation finalization."""
from __future__ import annotations

from agent.config import Config
from agent.events import EventEmitter
from agent.models import AgentMessageItem, Thread, Turn
from agent.store import ThreadStore

def finalize_cancelled(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    *,
    config: Config | None = None,
) -> None:
    turn.status = "cancelled"
    msg = AgentMessageItem(text="Turn cancelled by user.")
    turn.items.append(msg)
    store.append_item(thread, turn.id, msg)
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    if config is not None:
        from agent.notify import fire_notify

        fire_notify(
            config.notify,
            thread_id=thread.id,
            turn_id=turn.id,
            status=turn.status,
            cwd=config.cwd,
        )
