"""Turn budget and cost-cap termination."""
from __future__ import annotations

from agent.cancel import CancelToken
from agent.events import EventEmitter
from agent.models import AgentMessageItem, Thread, Turn
from agent.multi_agent.registry import WorkerRegistry
from agent.store import ThreadStore

def cost_cap_kill_turn(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    limit: float,
    observed: float,
) -> Turn:
    msg = AgentMessageItem(
        text=(
            f"Turn stopped: estimated cost ${observed:.4f} exceeded "
            f"max_cost_usd_per_turn=${limit:.4f}."
        )
    )
    turn.items.append(msg)
    store.append_item(thread, turn.id, msg)
    turn.status = "failed"
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.error(thread.id, msg.text)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    return turn


def budget_kill_turn(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    registry: WorkerRegistry | None,
    budget,
    metric: str,
    cancel: CancelToken,
) -> Turn:
    from agent.harness.active_turns import ActiveTurnRegistry

    snap = budget.snapshot
    emitter.multi_agent_budget_exceeded(
        thread.id,
        turn.id,
        metric=metric,
        limit=snap.last_limit,
        observed=snap.last_observed,
    )
    from agent.multi_agent.budget_state import save_budget_state

    save_budget_state(thread.id, budget.to_dict())
    if registry:
        with registry._lock:
            registry._pending_queue.clear()
            for rec in registry._workers.values():
                if rec.status in ("queued", "blocked", "running"):
                    rec.status = "cancelled"
                    rec._done.set()
        registry._dag_status = "cancelled"
    cancel.cancel()
    ActiveTurnRegistry.global_registry().cancel(thread.id)
    msg = (
        f"Supervisor stopped: budget exceeded ({metric}). "
        f"Snapshot: {budget.to_dict()}"
    )
    agent_item = AgentMessageItem(text=msg)
    turn.items.append(agent_item)
    store.append_item(thread, turn.id, agent_item)
    turn.status = "failed"
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    return turn
