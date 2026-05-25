from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.metrics import MetricsCollector
from agent.schedule.lock import acquire_tick_lock, lock_status, release_tick_lock
from agent.schedule.notifications import (
    build_notification_payload,
    notify_job_result,
    send_notification,
    sign_payload,
)
from agent.settings import ScheduleNotificationSettings


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch) -> None:
    MetricsCollector.reset_for_tests()
    monkeypatch.setattr("agent.schedule.lock.DEFAULT_LOCK_PATH", tmp_path / "tick.lock")
    yield
    release_tick_lock()
    MetricsCollector.reset_for_tests()


def test_build_notification_payload_failed() -> None:
    settings = ScheduleNotificationSettings()
    payload = build_notification_payload(
        "job1",
        {"ok": False, "error": "boom", "thread_id": "t1"},
        settings=settings,
    )
    assert payload["event"] == "schedule.job.failed"
    assert payload["job_id"] == "job1"


def test_build_notification_completed() -> None:
    settings = ScheduleNotificationSettings()
    payload = build_notification_payload("j", {"ok": True, "status": "completed"}, settings=settings)
    assert payload["event"] == "schedule.job.completed"


def test_build_notification_budget() -> None:
    settings = ScheduleNotificationSettings()
    payload = build_notification_payload(
        "j",
        {"ok": False, "error": "budget exceeded", "budget": {"metric": "x"}},
        settings=settings,
    )
    assert "budget" in payload


def test_snippet_truncated() -> None:
    settings = ScheduleNotificationSettings(include_transcript_snippet=True, max_snippet_chars=10)
    payload = build_notification_payload(
        "j",
        {"ok": False, "snippet": "x" * 100},
        settings=settings,
    )
    assert len(payload.get("snippet", "")) <= 10


def test_sign_payload() -> None:
    sig = sign_payload(b"body", "secret")
    assert sig.startswith("sha256=")


def test_send_log_only_when_no_url() -> None:
    settings = ScheduleNotificationSettings(enabled=True, webhook_url="")
    result = send_notification({"event": "schedule.job.failed", "job_id": "j"}, settings)
    assert result.get("log_only") is True


def test_send_skipped_when_disabled() -> None:
    settings = ScheduleNotificationSettings(enabled=False)
    result = send_notification({"event": "schedule.job.failed"}, settings)
    assert result.get("skipped") is True


def test_send_webhook_mocked() -> None:
    calls: list[dict] = []

    def fake_post(url, *, content, headers, timeout):
        calls.append({"url": url, "headers": headers, "body": content})
        return MagicMock(status_code=200)

    settings = ScheduleNotificationSettings(
        enabled=True,
        webhook_url="https://hooks.example.com/agent",
        webhook_secret_env="AGENT_SCHEDULE_WEBHOOK_SECRET",
        on_events=["failed"],
    )
    import os

    os.environ["AGENT_SCHEDULE_WEBHOOK_SECRET"] = "sec"
    payload = {"event": "schedule.job.failed", "job_id": "nightly"}
    result = send_notification(payload, settings, http_post=fake_post)
    assert result["ok"] is True
    assert calls
    assert "X-Agent-Signature" in calls[0]["headers"]


def test_send_retry_on_failure() -> None:
    attempts = {"n": 0}

    def flaky_post(*_a, **_k):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("network")
        return MagicMock(status_code=200)

    settings = ScheduleNotificationSettings(
        enabled=True,
        webhook_url="https://example.com/hook",
        retry_count=2,
        on_events=["failed"],
    )
    result = send_notification({"event": "schedule.job.failed"}, settings, http_post=flaky_post)
    assert result["ok"] is True
    assert attempts["n"] == 2


def test_send_event_filter() -> None:
    settings = ScheduleNotificationSettings(enabled=True, on_events=["completed"])
    result = send_notification({"event": "schedule.job.failed", "job_id": "x"}, settings)
    assert result.get("skipped") is True


def test_notify_job_result_wrapper() -> None:
    settings = ScheduleNotificationSettings(enabled=True, webhook_url="")
    result = notify_job_result("j1", {"ok": False, "error": "x"}, settings)
    assert result.get("log_only") is True


def test_notify_metrics_error() -> None:
    settings = ScheduleNotificationSettings(enabled=True, webhook_url="https://x", retry_count=0, on_events=["failed"])

    def fail(*_a, **_k):
        raise OSError("down")

    send_notification({"event": "schedule.job.failed"}, settings, http_post=fail)
    snap = MetricsCollector.global_collector().snapshot()
    assert snap.labeled_counters.get("agent_schedule_notify_total", {}).get("error", 0) >= 1


def test_lock_acquire_release() -> None:
    ok, msg = acquire_tick_lock()
    assert ok is True
    assert lock_status()["held"] is True
    release_tick_lock()
    assert lock_status()["held"] is False


def test_lock_second_acquire_skips() -> None:
    acquire_tick_lock()
    ok, msg = acquire_tick_lock()
    assert ok is False
    assert "lock" in msg
    release_tick_lock()


def test_lock_force_steals() -> None:
    acquire_tick_lock()
    ok, _ = acquire_tick_lock(force=True)
    assert ok is True
    release_tick_lock()


def test_lock_stale_steal(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / "tick.lock"
    monkeypatch.setattr("agent.schedule.lock.DEFAULT_LOCK_PATH", lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"acquired_at": time.time() - 500, "ttl_sec": 120}), encoding="utf-8")
    ok, _ = acquire_tick_lock(ttl_sec=120)
    assert ok is True
    release_tick_lock()


def test_lock_status_empty() -> None:
    assert lock_status()["held"] is False


def test_http_error_retries() -> None:
    settings = ScheduleNotificationSettings(
        enabled=True,
        webhook_url="https://x",
        retry_count=1,
        on_events=["failed"],
    )

    def bad_status(*_a, **_k):
        return MagicMock(status_code=500)

    result = send_notification({"event": "schedule.job.failed"}, settings, http_post=bad_status)
    assert result["ok"] is False
