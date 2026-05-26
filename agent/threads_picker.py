from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.models import Thread
from agent.profiles import thread_cost_summary
from agent.store import ThreadStore
from agent.tui.view_model import filter_session_threads, filter_threads_by_cwd
from agent.user_input import resolve_user_input


def list_threads_for_picker(
    store: ThreadStore,
    cwd: Path,
    *,
    filter_cwd: bool = True,
    meta_only: bool = False,
) -> list[Thread]:
    threads = store.list_thread_meta() if meta_only else store.list_threads()
    if filter_cwd:
        threads = filter_session_threads(threads, cwd)
    return threads


def thread_picker_label(thread: Thread, *, include_cost: bool = True) -> str:
    title = thread.display_label()
    updated = thread.updated_at[:19] if thread.updated_at else "—"
    if include_cost and thread.turns:
        cost = thread_cost_summary(thread)
        cost_part = (
            f"${cost['estimated_cost_usd']:.4f}"
            if cost.get("estimated_cost_usd")
            else "—"
        )
    else:
        cost_part = "—"
    return f"{title} | {thread.model} | {updated} | {cost_part}"


def thread_picker_label_summary(thread: Thread) -> str:
    """Fast label for meta-only threads (no cost — turns not loaded)."""
    return thread_picker_label(thread, include_cost=False)


def pick_thread(
    store: ThreadStore,
    cwd: Path,
    *,
    filter_cwd: bool = True,
    input_fn: Callable[[str], str] | None = None,
    headless: bool = False,
    auto_approve: bool = False,
) -> Thread | None:
    threads = list_threads_for_picker(store, cwd, filter_cwd=filter_cwd)
    if not threads:
        return None
    labels = [thread_picker_label(t) for t in threads]
    answer, selected, error = resolve_user_input(
        "Select a thread:",
        labels,
        allow_free_text=False,
        auto_approve=auto_approve,
        headless_json=headless,
        input_fn=input_fn,
    )
    if error or not selected:
        return None
    for thread, label in zip(threads, labels):
        if label == selected:
            return thread
    if answer and answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(threads):
            return threads[idx]
    return None


def pick_thread_or_last(
    store: ThreadStore,
    cwd: Path,
    *,
    use_last: bool = False,
    input_fn: Callable[[str], str] | None = None,
    headless: bool = False,
    auto_approve: bool = False,
) -> Thread | None:
    if use_last:
        threads = list_threads_for_picker(store, cwd)
        if not threads:
            return None
        return max(threads, key=lambda t: t.updated_at)
    return pick_thread(
        store,
        cwd,
        input_fn=input_fn,
        headless=headless,
        auto_approve=auto_approve,
    )


def thread_resume_preview(thread: Thread, *, max_len: int = 120) -> str:
    """One-line preview for TUI resume picker."""
    if thread.turns:
        for turn in reversed(thread.turns):
            for item in reversed(turn.items):
                if item.type == "userMessage":
                    text = " ".join(item.text.split())
                    if len(text) > max_len:
                        return text[: max_len - 1] + "…"
                    return text or "(empty message)"
    title = thread.title or thread.display_label()
    updated = thread.updated_at[:19] if thread.updated_at else "—"
    return f"{title} · {thread.model} · updated {updated}"


def format_thread_picker_table(threads: list[Thread]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, thread in enumerate(threads, 1):
        cost = thread_cost_summary(thread)
        rows.append(
            {
                "index": idx,
                "id": thread.id[:8] + "...",
                "title": thread.display_label(),
                "cwd": thread.cwd,
                "model": thread.model,
                "updated": thread.updated_at[:19] if thread.updated_at else "—",
                "cost": (
                    f"${cost['estimated_cost_usd']:.4f}"
                    if cost.get("estimated_cost_usd")
                    else "—"
                ),
            }
        )
    return rows
