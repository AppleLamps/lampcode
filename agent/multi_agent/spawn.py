from __future__ import annotations

from dataclasses import replace

from agent.config import Config
from agent.events import EventEmitter
from agent.models import AgentMessageItem, CollabSpawnItem, Thread
from agent.settings import MultiAgentSettings
from agent.store import ThreadStore

from agent.multi_agent.tools import SPAWN_WORKER_SCHEMA

MAX_WORKER_SUMMARY = 4000


def spawn_worker(
    parent_thread: Thread,
    arguments: dict,
    config: Config,
    store: ThreadStore,
    *,
    emitter: EventEmitter | None = None,
    run_turn_fn=None,
) -> tuple[str, CollabSpawnItem]:
    """Fork a worker thread and run one headless turn."""
    task = arguments.get("task", "")
    title = arguments.get("title")
    model = arguments.get("model") or config.model
    worker_backend = arguments.get("execution_backend")

    item = CollabSpawnItem(
        worker_thread_id="",
        task=task,
        status="running",
        title=title,
        model=model,
        execution_backend=worker_backend,
    )

    if emitter:
        emitter.collab_spawn_started(parent_thread.id, item.id, task=task)

    worker_thread = store.fork_thread(parent_thread, title=title or f"worker: {task[:40]}")
    item.worker_thread_id = worker_thread.id
    worker_thread.model = model

    worker_config = _worker_config(config, model=model, execution_backend=worker_backend)

    if run_turn_fn is None:
        from agent.events import EventEmitter as EE
        from agent.loop import run_turn

        def run_turn_fn(wt, prompt, cfg, st, **kwargs):
            return run_turn(wt, prompt, cfg, st, events=kwargs.get("events") or EE())

    try:
        turn = run_turn_fn(
            worker_thread,
            task,
            worker_config,
            store,
            events=emitter,
            session_auto_approve=config.multi_agent.worker_auto_approve,
        )
        summary = _extract_worker_summary(worker_thread, turn)
        item.status = "completed" if turn.status == "completed" else "failed"
        item.summary = summary
        text = summary or f"Worker {item.status} with no assistant message."
        if emitter:
            emitter.collab_spawn_completed(
                parent_thread.id,
                item.id,
                worker_thread_id=worker_thread.id,
                status=item.status,
            )
        return text[:MAX_WORKER_SUMMARY], item
    except Exception as exc:
        item.status = "failed"
        item.summary = str(exc)
        if emitter:
            emitter.collab_spawn_completed(
                parent_thread.id,
                item.id,
                worker_thread_id=worker_thread.id,
                status="failed",
            )
        return f"Worker spawn failed: {exc}", item


def _worker_config(
    config: Config,
    *,
    model: str,
    execution_backend: str | None,
    depth: int = 1,
) -> Config:
    execution = config.execution
    if execution_backend and config.multi_agent.inherit_execution_backend:
        execution = replace(execution, backend=execution_backend)
    elif not config.multi_agent.inherit_execution_backend:
        execution = replace(execution, backend="local")

    worker_ma_enabled = (
        config.multi_agent.allow_worker_spawn
        and depth < config.multi_agent.max_worker_depth
    )

    return Config(
        cwd=config.cwd,
        model=model,
        approval_mode="auto" if config.multi_agent.worker_auto_approve else config.approval_mode,
        max_rounds=config.max_rounds,
        command_timeout=config.command_timeout,
        max_tool_output=config.max_tool_output,
        prefer_ripgrep=config.prefer_ripgrep,
        context_window_tokens=config.context_window_tokens,
        compaction_threshold=config.compaction_threshold,
        sandbox_mode=config.sandbox_mode,
        exec_policy=config.exec_policy,
        compaction=config.compaction,
        openrouter=config.openrouter,
        recording=config.recording,
        isolation=config.isolation,
        web_search=config.web_search,
        execution=execution,
        multi_agent=MultiAgentSettings(
            enabled=worker_ma_enabled,
            max_workers_per_turn=config.multi_agent.max_workers_per_turn,
            max_worker_depth=config.multi_agent.max_worker_depth,
            max_concurrent_workers=config.multi_agent.max_concurrent_workers,
            worker_auto_approve=config.multi_agent.worker_auto_approve,
            inherit_execution_backend=config.multi_agent.inherit_execution_backend,
            wait_timeout_sec=config.multi_agent.wait_timeout_sec,
            allow_worker_spawn=config.multi_agent.allow_worker_spawn,
        ),
        openrouter_api_key=config.openrouter_api_key,
        openrouter_base_url=config.openrouter_base_url,
        config_path=config.config_path,
        skip_git_check=config.skip_git_check,
    )


def replace_worker_approval(config: Config) -> Config:
    from dataclasses import replace as dc_replace

    return dc_replace(config, approval_mode="auto")


def _extract_worker_summary(worker_thread: Thread, turn) -> str:
    for item in reversed(turn.items):
        if isinstance(item, AgentMessageItem) and item.text.strip():
            return item.text.strip()
    for t in reversed(worker_thread.turns):
        for item in reversed(t.items):
            if isinstance(item, AgentMessageItem) and item.text.strip():
                return item.text.strip()
    return ""


__all__ = ["spawn_worker", "SPAWN_WORKER_SCHEMA", "_extract_worker_summary", "_worker_config"]