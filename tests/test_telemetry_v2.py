from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config import Config
from agent.execution.local import LocalExecutionBackend
from agent.logging import log_event
from agent.metrics import MetricsCollector
from agent.settings import TelemetrySettings
from agent.telemetry.tracer import TracerProvider, memory_exporter


@pytest.fixture(autouse=True)
def _reset() -> None:
    TracerProvider.reset_for_tests()
    MetricsCollector.reset_for_tests()
    yield
    TracerProvider.reset_for_tests()
    MetricsCollector.reset_for_tests()


def test_sync_push_span_created() -> None:
    from agent.execution.sync import service as sync_service

    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.execution.backend = "local"
    cfg.execution.ssh.sync_enabled = False
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with patch.object(sync_service, "_prepare_remote_manifest", return_value=(None, "skip")):
        sync_service.run_sync_push(cfg, incremental=False, emitter=None)
    names = [s.name for s in memory_exporter().spans]
    assert "sync.push" in names


def test_sync_pull_span_name() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("sync.pull", thread_id="t1"):
        pass
    assert memory_exporter().spans[-1].name == "sync.pull"


def test_execution_local_run_span() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(enabled=True, sample_rate=1.0)
    TracerProvider.global_provider().configure(cfg.telemetry)
    backend = LocalExecutionBackend(cfg)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        backend.run(Path("."), "echo hi")
    names = [s.name for s in memory_exporter().spans]
    assert "execution.local.run" in names


def test_execution_ssh_run_span() -> None:
    from agent.execution.ssh import SshExecutionBackend

    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(enabled=True, sample_rate=1.0)
    TracerProvider.global_provider().configure(cfg.telemetry)
    backend = SshExecutionBackend(cfg)
    with patch.object(backend, "_execute_run") as mock_exec:
        mock_exec.return_value = MagicMock(exit_code=0, output="", duration_ms=1, backend="ssh", meta={})
        backend.run(Path("."), "echo")
    names = [s.name for s in memory_exporter().spans]
    assert "execution.ssh.run" in names


def test_log_event_includes_trace_id(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LOG_FORMAT", "json")
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    buf = StringIO()
    with patch("sys.stderr", buf):
        with provider.span("turn.run", thread_id="t1"):
            log_event("info", "test.event", thread_id="t1", turn_id="turn1")
    line = buf.getvalue().strip()
    payload = json.loads(line)
    assert "trace_id" in payload
    assert payload["thread_id"] == "t1"
    assert payload.get("service.name") == "agent-cli"


def test_histogram_export_parseable() -> None:
    mc = MetricsCollector.global_collector()
    mc.set_histogram_buckets([0.1, 1.0, 10.0])
    mc.observe("agent_turn_duration_seconds", 0.5)
    mc.observe("agent_tool_duration_seconds", 2.0, label="read_file")
    mc.observe("agent_http_request_duration_seconds", 0.05, label="/threads")
    body = mc.to_prometheus()
    assert "# TYPE agent_turn_duration_seconds histogram" in body
    assert 'tool="read_file"' in body
    assert 'path="/threads"' in body
    assert "_count" in body
    assert "_sum" in body


def test_errors_total_labeled() -> None:
    mc = MetricsCollector.global_collector()
    mc.inc_error("sync")
    body = mc.to_prometheus()
    assert 'component="sync"' in body


def test_get_trace_context_when_span_active() -> None:
    from agent.telemetry import get_trace_context

    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("turn.run"):
        ctx = get_trace_context()
        assert ctx.get("trace_id")
        assert ctx.get("span_id")
        assert ctx.get("service.name") == "agent-cli"


def test_approval_wait_span_names() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("approval.wait", tool="run_command"):
        with provider.span("approval.decision", tool="run_command", decision="accept"):
            pass
    names = [s.name for s in memory_exporter().spans]
    assert "approval.wait" in names
    assert "approval.decision" in names


def test_serve_http_request_span() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("serve.http.request", method="GET", path="/threads"):
        pass
    assert memory_exporter().spans[-1].attributes["method"] == "GET"


def test_export_runtime_metrics_buckets_from_config() -> None:
    from agent.telemetry import init_telemetry

    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(
        enabled=True,
        export_runtime_metrics=True,
        histogram_buckets_sec=[0.2, 2.0, 20.0],
    )
    init_telemetry(cfg)
    mc = MetricsCollector.global_collector()
    mc.observe("agent_turn_duration_seconds", 1.0)
    assert "agent_turn_duration_seconds" in mc.to_prometheus()


def test_sync_plan_span_via_decorator() -> None:
    from agent.execution.sync import service as sync_service

    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    with patch.object(sync_service, "_prepare_remote_manifest", return_value=(None, "n/a")):
        sync_service.run_sync_plan(cfg)
    names = [s.name for s in memory_exporter().spans]
    assert "sync.plan" in names


def test_fetch_remote_span() -> None:
    from agent.execution.sync import service as sync_service

    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.execution.ssh.sync.replicate_remote_state = False
    manifest = MagicMock(files=[])
    with patch.object(sync_service, "fetch_remote_manifest") as mock_fetch:
        mock_fetch.return_value = MagicMock(ok=True, manifest=manifest, error=None, missing=False)
        sync_service.run_sync_fetch_remote(cfg, thread_id="t1")
    names = [s.name for s in memory_exporter().spans]
    assert "sync.fetch_remote" in names


def test_metrics_json_includes_histograms() -> None:
    mc = MetricsCollector.global_collector()
    mc.observe("agent_turn_duration_seconds", 1.5)
    data = json.loads(mc.to_json())
    assert "histograms" in data
    assert data["histograms"]["agent_turn_duration_seconds"]
