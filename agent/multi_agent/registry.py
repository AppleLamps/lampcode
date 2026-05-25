from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent.config import Config
from agent.metrics import MetricsCollector
from agent.events import EventEmitter
from agent.models import AgentMessageItem, CollabWorkerItem, Thread
from agent.multi_agent.dag import (
    DagEdge,
    WorkerDagSnapshot,
    aggregate_dependency_summaries,
    deps_satisfied,
    detect_cycle,
    edges_from_dependencies,
)
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
    attempts: int = 0
    worker_dependencies: list[str] = field(default_factory=list)
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
            "worker_dependencies": list(self.worker_dependencies),
            "attempts": self.attempts,
            "error": self.error,
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
        self._edges: list[DagEdge] = []
        self._dag_status: str = "running"
        self._dag_started_at: float = time.monotonic()
        self._running = 0
        self._max_concurrent = config.multi_agent.max_concurrent_workers
        self._wait_timeout = config.multi_agent.wait_timeout_sec
        self.spawn_count = 0
        self._fail_fast_triggered = False
        self._program_id: str | None = None
        self._load_persisted_dag()

    @property
    def program_id(self) -> str | None:
        return self._program_id

    def _program_store(self):
        from agent.multi_agent.program_state import ProgramStore

        ct = self._config.multi_agent.cross_thread
        return ProgramStore(Path(ct.state_dir).expanduser())

    def _resolve_program_id(self, parent_thread: Thread) -> str:
        from agent.multi_agent.program_state import derive_program_id

        ct = self._config.multi_agent.cross_thread
        return derive_program_id(Path(parent_thread.cwd), auto=ct.program_id_auto)

    def _sync_program_node(
        self,
        parent_thread: Thread,
        record: WorkerRecord,
        *,
        program_scope: bool,
        turn_id: str | None = None,
    ) -> None:
        ct = self._config.multi_agent.cross_thread
        if not ct.enabled or not program_scope:
            return
        from agent.multi_agent.program_state import ProgramNode
        from agent.programs.sync.coordinator import get_coordinator

        program_id = self._resolve_program_id(parent_thread)
        coord = get_coordinator(self._config)
        if coord:
            coord.pull_if_stale(program_id)
        self._program_id = program_id
        edges = [e.to_dict() for e in self._edges if e.to == record.worker_id]
        node = ProgramNode(
            worker_id=record.worker_id,
            thread_id=parent_thread.id,
            turn_id=turn_id or self._turn_id or "",
            task=record.task,
            status=record.status,
            parent_thread_id=record.parent_thread_id,
        )
        store = self._program_store()
        store.upsert_node(
            program_id,
            node,
            edges=edges,
            thread_id=parent_thread.id,
            max_threads=ct.max_threads_linked,
        )
        MetricsCollector.global_collector().inc_labeled(
            "agent_program_dag_nodes_total", record.status, 1
        )
        if self._emitter:
            self._emitter.multi_agent_program_linked(
                parent_thread.id,
                turn_id or self._turn_id,
                program_id=program_id,
                worker_id=record.worker_id,
            )

    def _complete_program_node(self, parent_thread: Thread, record: WorkerRecord) -> None:
        ct = self._config.multi_agent.cross_thread
        if not ct.enabled or not self._program_id:
            return
        from agent.multi_agent.program_state import ProgramNode

        node = ProgramNode(
            worker_id=record.worker_id,
            thread_id=parent_thread.id,
            turn_id=self._turn_id or "",
            task=record.task,
            status=record.status,
            parent_thread_id=record.parent_thread_id,
        )
        self._program_store().upsert_node(self._program_id, node, thread_id=parent_thread.id)
        MetricsCollector.global_collector().inc_labeled(
            "agent_program_dag_nodes_total", record.status, 1
        )
        if self._emitter:
            self._emitter.multi_agent_program_node_completed(
                parent_thread.id,
                self._turn_id,
                program_id=self._program_id,
                worker_id=record.worker_id,
                status=record.status,
            )

    def _lookup_program_worker(self, worker_id: str) -> dict | None:
        ct = self._config.multi_agent.cross_thread
        if not ct.enabled:
            return None
        if not self._parent_thread:
            return None
        program_id = self._program_id or self._resolve_program_id(self._parent_thread)
        node = self._program_store().get_node(program_id, worker_id)
        if not node:
            return None
        return {
            "worker_id": node.worker_id,
            "status": node.status,
            "task": node.task,
            "thread_id": node.thread_id,
            "cross_thread": True,
        }

    def _load_persisted_dag(self) -> None:
        ma = self._config.multi_agent
        if not ma.dag_enabled or not ma.dag_persist_across_turns:
            return
        if not self._parent_thread:
            return
        from agent.metrics import MetricsCollector
        from agent.multi_agent.dag_state import DagStateStore

        store = DagStateStore()
        state = store.load(self._parent_thread.id if self._parent_thread else "")
        if not state:
            return
        if store.is_expired(state, ma.dag_max_age_sec):
            store.clear(state.thread_id)
            return
        for node in state.nodes:
            wid = node.get("worker_id", "")
            if not wid or wid in self._workers:
                continue
            rec = WorkerRecord(
                worker_id=wid,
                parent_thread_id=node.get("parent_thread_id", ""),
                task=node.get("task", ""),
                depth=int(node.get("depth", 0)),
                status=node.get("status", "queued"),
                worker_thread_id=node.get("worker_thread_id", ""),
                summary=node.get("summary"),
                execution_backend=node.get("execution_backend"),
                worker_dependencies=list(node.get("worker_dependencies", [])),
                attempts=int(node.get("attempts", 0)),
                error=node.get("error"),
            )
            if rec.status == "completed":
                rec._done.set()
            self._workers[wid] = rec
        from agent.multi_agent.dag import DagEdge

        self._edges = [DagEdge.from_dict(e) for e in state.edges]
        self._dag_status = state.dag_status
        self.spawn_count = max(self.spawn_count, state.spawn_count)
        statuses = self._statuses()
        deps_map = self._dependencies_map()
        for wid, rec in self._workers.items():
            if rec.status in ("completed", "failed", "cancelled", "running"):
                continue
            if self.dag_enabled and not deps_satisfied(wid, deps_map, statuses):
                rec.status = "blocked"
            elif rec.status == "queued":
                self._pending_queue.append(wid)
        MetricsCollector.global_collector().inc_labeled("agent_dag_persist_total", "load")
        if self._emitter and self._parent_thread and self._turn_id:
            self._emitter.multi_agent_dag_resumed_across_turns(
                self._parent_thread.id, self._turn_id, from_turn=state.last_turn_id
            )

    @property
    def dag_enabled(self) -> bool:
        return self._config.multi_agent.dag_enabled

    def _dependencies_map(self) -> dict[str, list[str]]:
        return {wid: list(rec.worker_dependencies) for wid, rec in self._workers.items()}

    def _statuses(self) -> dict[str, str]:
        return {wid: rec.status for wid, rec in self._workers.items()}

    def _emit_dag(self, event: str, worker_id: str, **extra) -> None:
        if not self._emitter or not self._parent_thread or not self._turn_id:
            return
        fn = getattr(self._emitter, event, None)
        if fn:
            fn(self._parent_thread.id, self._turn_id, worker_id, **extra)

    def _save_checkpoint(
        self,
        parent_thread: Thread,
        turn_id: str | None,
        messages: list | None = None,
    ) -> None:
        if not self._config.multi_agent.checkpoint_enabled or not turn_id:
            return
        from agent.multi_agent.checkpoint import save_checkpoint_from_registry

        path = save_checkpoint_from_registry(
            self,
            self._config,
            thread_id=parent_thread.id,
            turn_id=turn_id,
            spawn_count=self.spawn_count,
            messages=messages,
            dag_status=self._dag_status,
            edges=[e.to_dict() for e in self._edges],
        )
        if path and self._config.multi_agent.checkpoint_compact_after_workers > 0:
            if self.spawn_count % self._config.multi_agent.checkpoint_compact_after_workers == 0:
                from agent.multi_agent.checkpoint import compact_checkpoint_history

                compact_checkpoint_history(
                    parent_thread.id,
                    turn_id,
                    Path(self._config.multi_agent.checkpoint_dir).expanduser(),
                )
        if path and self._emitter:
            self._emitter.collab_checkpoint_saved(parent_thread.id, turn_id, path=str(path))
        if path and self._config.multi_agent.dag_persist_across_turns:
            from agent.metrics import MetricsCollector
            from agent.multi_agent.dag_state import DagStateStore

            DagStateStore().save_from_registry(
                self, thread_id=parent_thread.id, turn_id=turn_id or ""
            )
            MetricsCollector.global_collector().inc_labeled("agent_dag_persist_total", "save")
            if self._emitter:
                self._emitter.multi_agent_dag_persisted(parent_thread.id, turn_id or "")

    def _validate_new_deps(
        self,
        worker_id: str,
        depends_on: list[str],
        *,
        pending_batch: dict[str, list[str]] | None = None,
    ) -> str | None:
        known = set(self._workers.keys())
        if pending_batch:
            known |= set(pending_batch.keys())
        for dep in depends_on:
            if dep not in known:
                return f"dependency {dep!r} not found for worker {worker_id}"
            if dep == worker_id:
                return f"worker {worker_id} cannot depend on itself"
        trial_edges = list(self._edges)
        if pending_batch:
            trial_edges.extend(
                edges_from_dependencies(
                    list(pending_batch.keys()),
                    pending_batch,
                )
            )
        for dep in depends_on:
            cycle = detect_cycle(trial_edges, extra_from=dep, extra_to=worker_id)
            if cycle:
                if self._emitter and self._parent_thread and self._turn_id:
                    self._emitter.multi_agent_dag_cycle_rejected(
                        self._parent_thread.id,
                        self._turn_id,
                        worker_id=worker_id,
                        cycle=cycle,
                    )
                return f"cycle detected: {' -> '.join(cycle)}"
            trial_edges.append(DagEdge(from_id=dep, to_id=worker_id))
        return None

    def _register_worker_record(
        self,
        parent_thread: Thread,
        *,
        worker_id: str,
        task: str,
        depth: int,
        depends_on: list[str],
        title: str | None,
        model: str | None,
        worker_backend: str | None,
        item_id: str,
    ) -> WorkerRecord:
        for dep in depends_on:
            self._edges.append(DagEdge(from_id=dep, to_id=worker_id))

        deps_map = self._dependencies_map()
        deps_map[worker_id] = depends_on
        ready = (
            not self.dag_enabled
            or deps_satisfied(worker_id, deps_map, self._statuses())
        )
        status = "queued" if ready else "blocked"
        record = WorkerRecord(
            worker_id=worker_id,
            parent_thread_id=parent_thread.id,
            task=task,
            depth=depth,
            status=status,
            execution_backend=worker_backend,
            title=title,
            model=model,
            item_id=item_id,
            worker_dependencies=list(depends_on),
        )
        self._workers[worker_id] = record
        if ready:
            self._pending_queue.append(worker_id)
            self._emit_dag("multi_agent_dag_node_ready", worker_id)
        else:
            self._emit_dag(
                "multi_agent_dag_blocked",
                worker_id,
                waiting_on=depends_on,
            )
            MetricsCollector.global_collector().inc_labeled(
                "agent_workers_dag_nodes_total", "blocked", 1
            )
        MetricsCollector.global_collector().inc_labeled(
            "agent_workers_dag_nodes_total", status, 1
        )
        return record

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
        depends_on = list(arguments.get("depends_on") or [])
        program_scope = bool(arguments.get("program_scope", False))
        if not self.dag_enabled:
            depends_on = []

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

        if self.dag_enabled and depends_on:
            err = self._validate_new_deps(worker_id, depends_on)
            if err:
                collab = item or CollabWorkerItem(
                    worker_id=worker_id,
                    worker_thread_id="",
                    parent_thread_id=parent_thread.id,
                    task=task,
                    depth=depth,
                    status="failed",
                    summary=err,
                )
                return err, collab

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
        else:
            collab.worker_id = worker_id

        with self._lock:
            record = self._register_worker_record(
                parent_thread,
                worker_id=worker_id,
                task=task,
                depth=depth,
                depends_on=depends_on,
                title=title,
                model=model,
                worker_backend=worker_backend,
                item_id=collab.id,
            )
            collab.status = record.status  # type: ignore[assignment]

        self._sync_program_node(
            parent_thread,
            record,
            program_scope=program_scope,
            turn_id=turn_id,
        )

        MetricsCollector.global_collector().inc("workers_spawned")
        MetricsCollector.global_collector().inc_labeled("agent_workers_total", record.status, 1)

        self._pump_queue(parent_thread, turn_id or self._turn_id)

        msg = (
            f"Worker `{worker_id}` {record.status} (depth={depth}). "
            f"Task: {task[:200]}. Use wait_workers to collect results."
        )
        if depends_on:
            msg += f" depends_on={depends_on}."
        return msg, collab

    def enqueue_batch(
        self,
        parent_thread: Thread,
        tasks: list[dict],
        *,
        depth: int | None = None,
        turn_id: str | None = None,
    ) -> tuple[str, list[CollabWorkerItem]]:
        depth = depth if depth is not None else self._depth
        pending: dict[str, list[str]] = {}
        specs: list[dict] = []
        for spec in tasks:
            wid = spec.get("worker_id") or new_worker_id()
            deps = list(spec.get("depends_on") or [])
            if not self.dag_enabled:
                deps = []
            pending[wid] = deps
            specs.append({**spec, "worker_id": wid, "depends_on": deps})

        if self.dag_enabled:
            for wid, deps in pending.items():
                err = self._validate_new_deps(wid, deps, pending_batch=pending)
                if err:
                    return err, []

        collabs: list[CollabWorkerItem] = []
        with self._lock:
            for spec in specs:
                wid = spec["worker_id"]
                collab = CollabWorkerItem(
                    worker_id=wid,
                    worker_thread_id="",
                    parent_thread_id=parent_thread.id,
                    task=spec.get("task", ""),
                    depth=depth,
                    status="queued",
                    title=spec.get("title"),
                    model=spec.get("model") or self._config.model,
                    execution_backend=spec.get("execution_backend"),
                )
                record = self._register_worker_record(
                    parent_thread,
                    worker_id=wid,
                    task=collab.task,
                    depth=depth,
                    depends_on=spec["depends_on"],
                    title=collab.title,
                    model=collab.model,
                    worker_backend=collab.execution_backend,
                    item_id=collab.id,
                )
                collab.status = record.status  # type: ignore[assignment]
                collabs.append(collab)
                MetricsCollector.global_collector().inc("workers_spawned")
                MetricsCollector.global_collector().inc_labeled(
                    "agent_workers_total", record.status, 1
                )

        self._pump_queue(parent_thread, turn_id or self._turn_id)
        ids = [c.worker_id for c in collabs]
        return f"Batch registered {len(collabs)} workers: {ids}", collabs

    def get_worker_graph(self) -> str:
        snap = self.build_graph_snapshot()
        payload = snap.to_dict()
        ct = self._config.multi_agent.cross_thread
        if ct.enabled and self._parent_thread:
            program_id = self._program_id or self._resolve_program_id(self._parent_thread)
            payload["program_id"] = program_id
            payload["program_graph"] = self._program_store().graph_snapshot(program_id)
        return json.dumps(payload, indent=2)

    def build_graph_snapshot(self) -> WorkerDagSnapshot:
        with self._lock:
            nodes = [rec.to_dict() for rec in self._workers.values()]
            edges = [e.to_dict() for e in self._edges]
            status = self._dag_status
        return WorkerDagSnapshot(nodes=nodes, edges=edges, status=status)

    def _budget_exceeded(self) -> bool:
        budget = self._config.multi_agent.dag_wall_clock_budget_sec
        if budget <= 0:
            return False
        return (time.monotonic() - self._dag_started_at) > budget

    def _pump_queue(self, parent_thread: Thread, turn_id: str | None) -> None:
        if self._budget_exceeded():
            self._dag_status = "cancelled"
            return
        if self._fail_fast_triggered:
            return

        with self._lock:
            while self._running < self._max_concurrent and self._pending_queue:
                worker_id: str | None = None
                if self.dag_enabled:
                    for i, wid in enumerate(self._pending_queue):
                        rec = self._workers[wid]
                        if rec.status == "queued" and self._deps_ready(wid):
                            worker_id = wid
                            del self._pending_queue[i]
                            break
                    if worker_id is None:
                        break
                else:
                    worker_id = self._pending_queue.popleft()

                record = self._workers[worker_id]
                self._running += 1
                record.status = "running"
                self._emit_dag("multi_agent_dag_node_started", worker_id)
                t = threading.Thread(
                    target=self._run_worker,
                    args=(parent_thread, turn_id, worker_id),
                    daemon=True,
                )
                t.start()

    def _deps_ready(self, worker_id: str) -> bool:
        if not self.dag_enabled:
            return True
        return deps_satisfied(worker_id, self._dependencies_map(), self._statuses())

    def _promote_blocked_workers(self) -> None:
        if not self.dag_enabled:
            return
        for wid, rec in list(self._workers.items()):
            if rec.status == "blocked" and self._deps_ready(wid):
                rec.status = "queued"
                self._pending_queue.append(wid)
                self._emit_dag("multi_agent_dag_node_ready", wid)
                MetricsCollector.global_collector().inc_labeled(
                    "agent_workers_dag_nodes_total", "ready", 1
                )

    def _handle_fail_fast(self, failed_id: str) -> None:
        if not self._config.multi_agent.dag_fail_fast:
            return
        self._fail_fast_triggered = True
        self._dag_status = "failed"
        with self._lock:
            self._pending_queue.clear()
            for wid, rec in self._workers.items():
                if rec.status in ("queued", "blocked", "running") and wid != failed_id:
                    if rec.status == "running":
                        continue
                    rec.status = "cancelled"
                    rec.summary = f"cancelled due to fail-fast after {failed_id}"
                    rec._done.set()
                    MetricsCollector.global_collector().inc_labeled(
                        "agent_workers_dag_nodes_total", "cancelled", 1
                    )

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

        task_prompt = record.task
        if self.dag_enabled and record.worker_dependencies:
            ctx = aggregate_dependency_summaries(
                worker_id,
                self._dependencies_map(),
                {wid: r.summary for wid, r in self._workers.items()},
            )
            if ctx:
                task_prompt = ctx + record.task

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
                registry._edges = list(self._edges)
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
                task_prompt,
                worker_config,
                self._store,
                events=self._emitter,
                session_auto_approve=self._config.multi_agent.worker_auto_approve,
            )
            record.summary = _extract_worker_summary(worker_thread, turn)
            record.status = "completed" if turn.status == "completed" else "failed"
            if record.attempts > 0:
                if record.status == "completed":
                    MetricsCollector.global_collector().inc("workers_retry_success")
                else:
                    MetricsCollector.global_collector().inc("workers_retry_failed")
            MetricsCollector.global_collector().inc_labeled(
                "agent_workers_total", record.status, 1
            )
            self._complete_program_node(parent_thread, record)
            self._emit_dag("multi_agent_dag_node_completed", worker_id, status=record.status)
            MetricsCollector.global_collector().inc_labeled(
                "agent_workers_dag_nodes_total", record.status, 1
            )
            if record.status == "failed":
                self._handle_fail_fast(worker_id)
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
            if record.attempts > 0:
                MetricsCollector.global_collector().inc("workers_retry_failed")
            MetricsCollector.global_collector().inc_labeled("agent_workers_total", "failed", 1)
            self._emit_dag("multi_agent_dag_node_completed", worker_id, status="failed")
            self._handle_fail_fast(worker_id)
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
            self._promote_blocked_workers()
            self._update_dag_status()
            self._save_checkpoint(parent_thread, turn_id)
            self._pump_queue(parent_thread, turn_id)

    def _update_dag_status(self) -> None:
        if not self.dag_enabled or not self._workers:
            return
        statuses = self._statuses()
        if any(s == "failed" for s in statuses.values()) and self._config.multi_agent.dag_fail_fast:
            self._dag_status = "failed"
            return
        if all(s in ("completed", "cancelled", "failed") for s in statuses.values()):
            if any(s == "failed" for s in statuses.values()):
                self._dag_status = "failed"
            elif any(s == "cancelled" for s in statuses.values()):
                self._dag_status = "cancelled"
            else:
                self._dag_status = "completed"

    def _expand_deps(self, worker_ids: list[str]) -> list[str]:
        expanded: set[str] = set()
        stack = list(worker_ids)
        deps_map = self._dependencies_map()
        while stack:
            wid = stack.pop()
            if wid in expanded:
                continue
            expanded.add(wid)
            stack.extend(deps_map.get(wid, []))
        return list(expanded)

    def wait_workers(
        self,
        worker_ids: list[str] | None = None,
        *,
        timeout_sec: int | None = None,
        mode: str = "all",
    ) -> str:
        timeout = timeout_sec or self._wait_timeout
        targets = worker_ids or list(self._workers.keys())
        if mode == "deps" and worker_ids:
            targets = self._expand_deps(worker_ids)
        if not targets:
            return json.dumps({"workers": [], "message": "No workers registered.", "mode": mode})

        deadline = time.monotonic() + timeout
        results: list[dict] = []

        if mode == "any":
            while time.monotonic() < deadline:
                for wid in targets:
                    record = self._workers.get(wid)
                    if record and record._done.is_set():
                        results.append(self._worker_result(wid, record))
                        return json.dumps({"workers": results, "mode": mode}, indent=2)
                time.sleep(0.05)
            for wid in targets:
                record = self._workers.get(wid)
                if record and not record._done.is_set():
                    record.status = "timed_out"
                    results.append(
                        {
                            "worker_id": wid,
                            "status": "timed_out",
                            "task": record.task,
                        }
                    )
            return json.dumps({"workers": results, "mode": mode}, indent=2)

        for wid in targets:
            record = self._workers.get(wid)
            if not record:
                prog = self._lookup_program_worker(wid)
                if prog:
                    results.append(prog)
                    continue
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
            results.append(self._worker_result(wid, record))

        return json.dumps({"workers": results, "mode": mode}, indent=2)

    def _worker_result(self, wid: str, record: WorkerRecord) -> dict:
        return {
            "worker_id": wid,
            "status": record.status,
            "task": record.task,
            "summary": (record.summary or "")[:2000],
            "worker_thread_id": record.worker_thread_id,
            "worker_dependencies": list(record.worker_dependencies),
        }

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
