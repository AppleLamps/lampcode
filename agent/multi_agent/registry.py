from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from agent.config import Config
from agent.events import EventEmitter
from agent.models import AgentMessageItem, CollabWorkerItem, Thread
from agent.multi_agent.spawn import _extract_worker_summary, _worker_config
from agent.store import ThreadStore


def new_worker_id() -> str:
    return f"w-{uuid.uuid4().hex[:8]}"


@dataclass
class WorkerRecord:
    worker_id: str
    parent_thread_id: str
    task: str
    depth: int
    status: str = "queued"
    worker_thread_id: str = ""
    summary: str | None = None
    execution_backend: str | None = None
    title: str | None = None
    model: str | None = None
    item_id: str = ""
    error: str | None = None
    _done: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "worker_thread_id": self.worker_thread_id,
            "parent_thread_id": self.parent_thread_id,
            "task": self.task,
            "depth": self.depth,
            "status": self.status,
            "summary": self.summary,
            "execution_backend": self.execution_backend,
        }


class WorkerRegistry:
    """In-memory worker queue and registry for a supervisor turn."""

    def __init__(
        self,
        config: Config,
        store: ThreadStore,
        *,
        emitter: EventEmitter | None = None,
        parent_thread: Thread | None = None,
        turn_id: str | None = None,
        depth: int = 0,
        run_turn_fn: Callable | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._emitter = emitter
        self._parent_thread = parent_thread
        self._turn_id = turn_id
        self._depth = depth
        self._run_turn_fn = run_turn_fn
        self._lock = threading.Lock()
        self._workers: dict[str, WorkerRecord] = {}
        self._pending_queue: deque[str] = deque()
        self._running = 0
        self._max_concurrent = config.multi_agent.max_concurrent_workers
        self._wait_timeout = config.multi_agent.wait_timeout_sec

    def enqueue(
        self,
        parent_thread: Thread,
        arguments: dict,
        *,
        depth: int | None = None,
        turn_id: str | None = None,
        item: CollabWorkerItem | None = None,
    ) -> tuple[str, CollabWorkerItem]:
        depth = depth if depth is not None else self._depth
        task = arguments.get("task", "")
        worker_id = arguments.get("worker_id") or new_worker_id()
        title = arguments.get("title")
        model = arguments.get("model") or self._config.model
        worker_backend = arguments.get("execution_backend")

        if depth >= self._config.multi_agent.max_worker_depth:
            collab = item or CollabWorkerItem(
                worker_id=worker_id,
                worker_thread_id="",
                parent_thread_id=parent_thread.id,
                task=task,
                depth=depth,
                status="failed",
                summary=f"Worker depth limit reached (max {self._config.multi_agent.max_worker_depth}).",
            )
            return collab.summary or "Depth limit reached.", collab

        if not self._config.multi_agent.allow_worker_spawn and depth > 0:
            collab = item or CollabWorkerItem(
                worker_id=worker_id,
                worker_thread_id="",
                parent_thread_id=parent_thread.id,
                task=task,
                depth=depth,
                status="failed",
                summary="Worker spawn disabled (allow_worker_spawn=false).",
            )
            return collab.summary or "Spawn disabled.", collab

        collab = item or CollabWorkerItem(
            worker_id=worker_id,
            worker_thread_id="",
            parent_thread_id=parent_thread.id,
            task=task,
            depth=depth,
            status="queued",
            title=title,
            model=model,
            execution_backend=worker_backend,
        )
        if item:
            collab.worker_id = worker_id
            collab.task = task
            collab.depth = depth
            collab.status = "queued"
        else:
            collab.worker_id = worker_id

        record = WorkerRecord(
            worker_id=worker_id,
            parent_thread_id=parent_thread.id,
            task=task,
            depth=depth,
            status="queued",
            execution_backend=worker_backend,
            title=title,
            model=model,
            item_id=collab.id,
        )

        with self._lock:
            self._workers[worker_id] = record
            self._pending_queue.append(worker_id)

        self._pump_queue(parent_thread, turn_id or self._turn_id)

        msg = (
            f"Worker `{worker_id}` queued (depth={depth}). "
            f"Task: {task[:200]}. Use wait_workers to collect results."
        )
        return msg, collab

    def _pump_queue(self, parent_thread: Thread, turn_id: str | None) -> None:
        with self._lock:
            while self._running < self._max_concurrent and self._pending_queue:
                worker_id = self._pending_queue.popleft()
                record = self._workers[worker_id]
                self._running += 1
                record.status = "running"
                t = threading.Thread(
                    target=self._run_worker,
                    args=(parent_thread, turn_id, worker_id),
                    daemon=True,
                )
                t.start()

    def _run_worker(
        self, parent_thread: Thread, turn_id: str | None, worker_id: str
    ) -> None:
        record = self._workers[worker_id]
        if self._emitter:
            self._emitter.collab_spawn_started(
                parent_thread.id, record.item_id, task=record.task
            )

        worker_thread = self._store.fork_thread(
            parent_thread, title=record.title or f"worker: {record.task[:40]}"
        )
        record.worker_thread_id = worker_thread.id
        worker_thread.model = record.model or self._config.model

        worker_config = _worker_config(
            self._config,
            model=worker_thread.model,
            execution_backend=record.execution_backend,
            depth=record.depth + 1,
        )

        run_turn_fn = self._run_turn_fn
        if run_turn_fn is None:
            from agent.events import EventEmitter as EE
            from agent.loop import run_turn

            def run_turn_fn(wt, prompt, cfg, st, **kwargs):
                registry = WorkerRegistry(
                    cfg,
                    st,
                    emitter=kwargs.get("events") or EE(),
                    parent_thread=wt,
                    turn_id=turn_id,
                    depth=record.depth + 1,
                    run_turn_fn=self._run_turn_fn,
                )
                return run_turn(
                    wt,
                    prompt,
                    cfg,
                    st,
                    events=kwargs.get("events") or EE(),
                    worker_registry=registry,
                    worker_depth=record.depth + 1,
                    session_auto_approve=self._config.multi_agent.worker_auto_approve,
                )

        try:
            turn = run_turn_fn(
                worker_thread,
                record.task,
                worker_config,
                self._store,
                events=self._emitter,
                session_auto_approve=self._config.multi_agent.worker_auto_approve,
            )
            record.summary = _extract_worker_summary(worker_thread, turn)
            record.status = "completed" if turn.status == "completed" else "failed"
            if self._emitter:
                self._emitter.collab_spawn_completed(
                    parent_thread.id,
                    record.item_id,
                    worker_thread_id=worker_thread.id,
                    status=record.status,
                    worker_id=worker_id,
                )
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            record.summary = str(exc)
            if self._emitter:
                self._emitter.collab_spawn_completed(
                    parent_thread.id,
                    record.item_id,
                    worker_thread_id=worker_thread.id,
                    status="failed",
                    worker_id=worker_id,
                )
        finally:
            with self._lock:
                self._running -= 1
            record._done.set()
            self._pump_queue(parent_thread, turn_id)

    def wait_workers(
        self,
        worker_ids: list[str] | None = None,
        *,
        timeout_sec: int | None = None,
    ) -> str:
        timeout = timeout_sec or self._wait_timeout
        targets = worker_ids or list(self._workers.keys())
        if not targets:
            return json.dumps({"workers": [], "message": "No workers registered."})

        deadline = time.monotonic() + timeout
        results: list[dict] = []

        for wid in targets:
            record = self._workers.get(wid)
            if not record:
                results.append({"worker_id": wid, "status": "not_found"})
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if not record._done.wait(timeout=remaining):
                record.status = "timed_out"
                results.append(
                    {
                        "worker_id": wid,
                        "status": "timed_out",
                        "task": record.task,
                    }
                )
                continue
            results.append(
                {
                    "worker_id": wid,
                    "status": record.status,
                    "task": record.task,
                    "summary": (record.summary or "")[:2000],
                    "worker_thread_id": record.worker_thread_id,
                }
            )

        return json.dumps({"workers": results}, indent=2)

    def list_workers(self) -> str:
        with self._lock:
            rows = [r.to_dict() for r in self._workers.values()]
        return json.dumps({"workers": rows}, indent=2)

    def update_collab_item(self, collab: CollabWorkerItem, worker_id: str) -> None:
        record = self._workers.get(worker_id)
        if not record:
            return
        collab.worker_thread_id = record.worker_thread_id
        collab.status = record.status  # type: ignore[assignment]
        collab.summary = record.summary
