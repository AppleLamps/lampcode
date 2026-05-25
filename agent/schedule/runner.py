from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from agent.config import Config
from agent.events import EventEmitter
from agent.schedule.cron import is_job_due
from agent.schedule.guards import validate_job_for_run
from agent.schedule.store import ScheduleJob, ScheduleStore, write_run_history
from agent.settings import ScheduleSettings


def run_due_jobs(
    store: ScheduleStore,
    settings: ScheduleSettings,
    *,
    run_job_fn: Callable[[ScheduleJob, Config], dict[str, Any]] | None = None,
    config: Config | None = None,
    emitter: EventEmitter | None = None,
    force_job_id: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cfg = config or Config.resolve()
    jobs = [store.get(force_job_id)] if force_job_id else store.list_jobs()
    for job in jobs:
        if job is None:
            continue
        if force_job_id is None and not is_job_due(job):
            continue
        ok, reason = validate_job_for_run(
            job,
            settings,
            config_multi_agent_budgets_enabled=cfg.multi_agent.budgets.enabled,
        )
        if not ok:
            results.append({"job_id": job.id, "ok": False, "error": reason, "skipped": True})
            continue
        started = time.time()
        if emitter:
            emitter.schedule_job_started(job.id, cwd=job.cwd)
        try:
            runner = run_job_fn or _default_run_job
            payload = runner(job, cfg)
            payload["started_at"] = started
            payload["duration_sec"] = round(time.time() - started, 2)
            store.update_last_run(job.id, started)
            write_run_history(job.id, payload)
            if emitter:
                emitter.schedule_job_completed(job.id, status=payload.get("status", "unknown"))
            results.append({"job_id": job.id, "ok": True, **payload})
        except Exception as exc:
            err = {"job_id": job.id, "ok": False, "error": str(exc), "started_at": started}
            write_run_history(job.id, err)
            if emitter:
                emitter.schedule_job_failed(job.id, error=str(exc))
            results.append(err)
    return results


def _default_run_job(job: ScheduleJob, config: Config) -> dict[str, Any]:
    from agent.loop import run_turn
    from agent.models import Thread, new_id
    from agent.store import ThreadStore

    cwd = Path(job.cwd).expanduser()
    ma = config.multi_agent
    ma.enabled = True
    run_cfg = Config.resolve(
        cwd=cwd,
        multi_agent=True,
    )
    store = ThreadStore()
    thread = Thread(id=new_id(), cwd=str(cwd.resolve()), model=run_cfg.model)
    store.create_thread(thread)
    turn = run_turn(
        thread,
        job.prompt,
        run_cfg,
        store,
        events=EventEmitter(),
        budget_profile=job.budget_profile,
    )
    return {
        "thread_id": thread.id,
        "turn_id": turn.id,
        "status": turn.status,
    }
