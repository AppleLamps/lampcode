from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable

from agent.metrics import MetricsCollector
from agent.settings import ScheduleNotificationSettings


def _event_name(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        if result.get("skipped"):
            return "schedule.job.skipped"
        return "schedule.job.failed"
    status = str(result.get("status", "")).lower()
    if "budget" in str(result.get("error", "")).lower():
        return "schedule.job.budget_exceeded"
    if status == "failed":
        return "schedule.job.failed"
    return "schedule.job.completed"


def build_notification_payload(
    job_id: str,
    result: dict[str, Any],
    *,
    settings: ScheduleNotificationSettings,
) -> dict[str, Any]:
    event = _event_name(result)
    payload: dict[str, Any] = {
        "event": event,
        "job_id": job_id,
        "thread_id": result.get("thread_id"),
        "turn_id": result.get("turn_id"),
        "error": result.get("error"),
        "status": result.get("status"),
        "duration_sec": result.get("duration_sec"),
        "timestamp": time.time(),
    }
    if result.get("budget"):
        payload["budget"] = result["budget"]
    if settings.include_transcript_snippet and result.get("snippet"):
        snippet = str(result["snippet"])
        payload["snippet"] = snippet[: settings.max_snippet_chars]
    return payload


def sign_payload(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def send_notification(
    payload: dict[str, Any],
    settings: ScheduleNotificationSettings,
    *,
    http_post: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not settings.enabled:
        return {"ok": False, "skipped": True, "reason": "notifications disabled"}

    event_key = payload.get("event", "").replace("schedule.job.", "")
    allowed = {f"schedule.job.{e}" if not e.startswith("schedule.") else e for e in settings.on_events}
    allowed_simple = set(settings.on_events) | {f"schedule.job.{e}" for e in settings.on_events}
    if payload.get("event") not in allowed_simple:
        return {"ok": True, "skipped": True, "reason": f"event {event_key} not in on_events"}

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    if not settings.webhook_url:
        MetricsCollector.global_collector().inc_labeled("agent_schedule_notify_total", "log_only")
        return {"ok": True, "log_only": True, "payload": payload}

    secret = os.environ.get(settings.webhook_secret_env, "")
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Agent-Signature"] = sign_payload(body, secret)

    post = http_post or _default_post
    last_error = ""
    attempts = max(1, settings.retry_count + 1)
    for attempt in range(attempts):
        try:
            resp = post(settings.webhook_url, content=body, headers=headers, timeout=settings.timeout_sec)
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                last_error = f"HTTP {status}"
                continue
            MetricsCollector.global_collector().inc_labeled("agent_schedule_notify_total", "ok")
            return {"ok": True, "attempts": attempt + 1}
        except Exception as exc:
            last_error = str(exc)
    MetricsCollector.global_collector().inc_labeled("agent_schedule_notify_total", "error")
    return {"ok": False, "error": last_error}


def _default_post(url: str, *, content: bytes, headers: dict, timeout: int) -> Any:
    import httpx

    return httpx.post(url, content=content, headers=headers, timeout=timeout)


def notify_job_result(
    job_id: str,
    result: dict[str, Any],
    settings: ScheduleNotificationSettings,
    *,
    http_post: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    payload = build_notification_payload(job_id, result, settings=settings)
    return send_notification(payload, settings, http_post=http_post)
