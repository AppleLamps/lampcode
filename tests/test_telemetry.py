from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.settings import TelemetrySettings
from agent.telemetry.tracer import TracerProvider, memory_exporter


@pytest.fixture(autouse=True)
def _reset_tracer() -> None:
    TracerProvider.reset_for_tests()
    yield
    TracerProvider.reset_for_tests()


def test_disabled_telemetry_no_spans() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(enabled=False)
    provider = TracerProvider.global_provider()
    provider.configure(cfg.telemetry)
    with provider.span("turn.run", thread_id="t1"):
        pass
    assert len(memory_exporter().spans) == 0


def test_enabled_inmemory_span() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(enabled=True, sample_rate=1.0)
    provider = TracerProvider.global_provider()
    provider.configure(cfg.telemetry)
    with provider.span("turn.run", thread_id="t1", tool="read_file"):
        pass
    assert len(memory_exporter().spans) == 1
    assert memory_exporter().spans[0].name == "turn.run"


def test_sample_rate_zero() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(enabled=True, sample_rate=0.0)
    provider = TracerProvider.global_provider()
    provider.configure(cfg.telemetry)
    with provider.span("turn.run"):
        pass
    assert len(memory_exporter().spans) == 0


def test_tool_execute_span_attributes() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("tool.execute", tool="run_command", backend="local"):
        pass
    span = memory_exporter().spans[-1]
    assert span.attributes["tool"] == "run_command"


def test_init_telemetry_from_config() -> None:
    from agent.telemetry import init_telemetry

    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.telemetry = TelemetrySettings(enabled=True)
    init_telemetry(cfg)
    provider = TracerProvider.global_provider()
    assert provider.enabled is True


def test_env_enables_telemetry(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OTEL_ENABLED", "1")
    from agent.config import Config as C

    cfg = C.resolve()
    assert cfg.telemetry.enabled is True


def test_otlp_endpoint_env(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://example:4318/v1/traces")
    from agent.config import Config as C

    cfg = C.resolve()
    assert cfg.telemetry.otlp_endpoint == "http://example:4318/v1/traces"


def test_export_console_flag() -> None:
    provider = TracerProvider.global_provider()
    provider.configure(
        TelemetrySettings(enabled=True, export_console=True, sample_rate=1.0)
    )
    with provider.span("sync.push"):
        pass
    assert memory_exporter().spans[-1].name == "sync.push"


def test_nested_spans_memory() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("turn.run"):
        with provider.span("tool.execute", tool="a"):
            pass
    assert len(memory_exporter().spans) == 2


def test_span_error_status() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with pytest.raises(ValueError):
        with provider.span("worker.spawn"):
            raise ValueError("boom")
    assert memory_exporter().spans[-1].status == "error"


def test_get_trace_context_empty() -> None:
    from agent.telemetry import get_trace_context

    assert get_trace_context() == {}


def test_model_completion_span_via_openrouter() -> None:
    from model.openrouter import OpenRouterClient

    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="test-key")
    cfg.telemetry = TelemetrySettings(enabled=True, sample_rate=1.0)
    TracerProvider.global_provider().configure(cfg.telemetry)
    client = OpenRouterClient(cfg)

    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}]}

    with patch.object(client, "_request_with_retry", return_value=FakeResp()):
        assert client.complete([{"role": "user", "content": "hi"}]) == "hi"
    names = [s.name for s in memory_exporter().spans]
    assert "model.completion" in names


def test_worker_wait_span_names() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    with provider.span("worker.wait", mode="all"):
        pass
    assert memory_exporter().spans[-1].name == "worker.wait"


def test_should_sample_full() -> None:
    provider = TracerProvider.global_provider()
    provider.enabled = True
    provider.sample_rate = 1.0
    assert provider.should_sample() is True


def test_configure_without_otel_packages() -> None:
    provider = TracerProvider.global_provider()
    provider.configure(TelemetrySettings(enabled=True, sample_rate=1.0))
    assert provider._otel_available is False
    with provider.span("turn.run"):
        pass
    assert len(memory_exporter().spans) == 1
