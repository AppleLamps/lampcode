from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from agent.events import EventEmitter
from agent.metrics import MetricsCollector
from agent.schedule.cron import cron_matches, is_job_due, parse_cron_field
from agent.schedule.guards import acknowledge_unattended_risk, validate_job_for_run
from agent.schedule.runner import run_due_jobs
from agent.schedule.store import ScheduleJob, ScheduleStore, list_run_history, write_run_history
from agent.settings import ScheduleSettings


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    yield
    MetricsCollector.reset_for_tests()


def test_cron_star_matches() -> None:
    when = dt.datetime(2026, 5, 25, 14, 30)
    assert cron_matches("* * * * *", when)


def test_cron_hourly() -> None:
    when = dt.datetime(2026, 5, 25, 2, 0)
    assert cron_matches("0 2 * * *", when)
    assert not cron_matches("0 3 * * *", when)


def test_cron_every_5_minutes() -> None:
    when = dt.datetime(2026, 5, 25, 10, 10)
    assert cron_matches("*/5 * * * *", when)
    when2 = dt.datetime(2026, 5, 25, 10, 11)
    assert not cron_matches("*/5 * * * *", when2)


def test_parse_cron_field_step() -> None:
    vals = parse_cron_field("*/15", 0, 59)
    assert 0 in vals and 15 in vals


def test_is_job_due_first_run() -> None:
    job = ScheduleJob(id="j1", cron="* * * * *", last_run_at=0)
    assert is_job_due(job)


def test_is_job_due_disabled() -> None:
    job = ScheduleJob(id="j1", enabled=False, cron="* * * * *")
    assert not is_job_due(job)


def test_is_job_due_recent_run() -> None:
    job = ScheduleJob(id="j1", cron="* * * * *", last_run_at=dt.datetime.now().timestamp())
    assert not is_job_due(job)


def test_schedule_store_add_list(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.json")
    job = ScheduleJob(id="nightly", prompt="test", cron="0 2 * * *")
    store.add(job)
    assert store.get("nightly") is not None
    assert len(store.list_jobs()) == 1


def test_schedule_store_remove(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.json")
    store.add(ScheduleJob(id="x"))
    assert store.remove("x")
    assert store.get("x") is None


def test_budget_guard_rejects_off_profile() -> None:
    job = ScheduleJob(id="j", budget_profile="off")
    settings = ScheduleSettings(enabled=True, require_budgets=True)
    ok, reason = validate_job_for_run(job, settings)
    assert not ok
    assert "budget" in reason.lower()


def test_budget_guard_disabled_schedule() -> None:
    job = ScheduleJob(id="j", budget_profile="strict")
    settings = ScheduleSettings(enabled=False)
    ok, reason = validate_job_for_run(job, settings)
    assert not ok


def test_multi_agent_required() -> None:
    job = ScheduleJob(id="j", multi_agent=False, budget_profile="strict")
    settings = ScheduleSettings(enabled=True, require_multi_agent=True)
    ok, reason = validate_job_for_run(job, settings)
    assert not ok


def test_auto_approval_blocked() -> None:
    job = ScheduleJob(id="j", budget_profile="strict")
    settings = ScheduleSettings(
        enabled=True,
        default_approval_mode="auto",
        allow_unattended_auto=False,
    )
    ok, reason = validate_job_for_run(job, settings)
    assert not ok


def test_auto_approval_requires_ack_file(tmp_path: Path, monkeypatch) -> None:
    ack = tmp_path / "ack"
    monkeypatch.setattr(
        "agent.schedule.guards.RISKY_ACK_PATH",
        ack,
    )
    job = ScheduleJob(id="j", budget_profile="strict")
    settings = ScheduleSettings(
        enabled=True,
        default_approval_mode="auto",
        allow_unattended_auto=True,
    )
    ok, reason = validate_job_for_run(job, settings)
    assert not ok
    ack.write_text("ok\n")
    ok2, _ = validate_job_for_run(job, settings)
    assert ok2


def test_acknowledge_unattended_risk(tmp_path: Path, monkeypatch) -> None:
    ack = tmp_path / "ack"
    monkeypatch.setattr("agent.schedule.guards.RISKY_ACK_PATH", ack)
    acknowledge_unattended_risk()
    assert ack.is_file()


def test_write_run_history(tmp_path: Path) -> None:
    p = write_run_history("job1", {"status": "ok", "started_at": 1000}, base=tmp_path)
    assert p.is_file()
    hist = list_run_history("job1", base=tmp_path)
    assert len(hist) == 1


def test_run_due_jobs_mocked(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.json")
    store.add(ScheduleJob(id="due-job", cron="* * * * *", budget_profile="strict", last_run_at=0))
    settings = ScheduleSettings(enabled=True, require_budgets=True)
    calls: list[str] = []

    def fake_run(job, cfg):
        calls.append(job.id)
        return {"status": "completed", "thread_id": "t1"}

    results = run_due_jobs(
        store,
        settings,
        run_job_fn=fake_run,
        emitter=EventEmitter(),
    )
    assert calls == ["due-job"]
    assert results[0]["ok"] is True


def test_run_due_skipped_no_budget(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.json")
    store.add(ScheduleJob(id="bad", cron="* * * * *", budget_profile="off", last_run_at=0))
    settings = ScheduleSettings(enabled=True, require_budgets=True)
    results = run_due_jobs(store, settings, run_job_fn=lambda j, c: {"status": "ok"})
    assert results[0].get("skipped") is True


def test_force_run_job(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.json")
    store.add(ScheduleJob(id="forced", cron="0 0 1 1 *", budget_profile="strict"))
    settings = ScheduleSettings(enabled=True)
    results = run_due_jobs(
        store,
        settings,
        force_job_id="forced",
        run_job_fn=lambda j, c: {"status": "done"},
    )
    assert results[0]["ok"] is True


def test_schedule_persistence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "schedules.json"
    store = ScheduleStore(path)
    store.add(ScheduleJob(id="persist", prompt="hello", cron="0 1 * * *"))
    store2 = ScheduleStore(path)
    assert store2.get("persist").prompt == "hello"


def test_validate_passes_strict_job() -> None:
    job = ScheduleJob(id="j", budget_profile="strict", multi_agent=True)
    settings = ScheduleSettings(enabled=True)
    ok, _ = validate_job_for_run(job, settings, config_multi_agent_budgets_enabled=True)
    assert ok
