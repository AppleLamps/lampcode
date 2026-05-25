from __future__ import annotations

import random
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class SpanRecord:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    children: list[SpanRecord] = field(default_factory=list)
    trace_id: str = ""
    span_id: str = ""


class InMemorySpanExporter:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []

    def export(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def clear(self) -> None:
        self.spans.clear()


class NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        return

    def set_status(self, status: str) -> None:
        return

    def end(self) -> None:
        return


class TracerProvider:
    _instance: TracerProvider | None = None

    def __init__(self) -> None:
        self.enabled = False
        self.service_name = "agent-cli"
        self.service_version = "1.2.0"
        self.sample_rate = 1.0
        self.export_console = False
        self._memory = InMemorySpanExporter()
        self._otel_available = False
        self._tracer = None
        self._current_trace: dict[str, str] = {}
        self._span_stack: list[str] = []

    @classmethod
    def global_provider(cls) -> TracerProvider:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def configure(self, settings) -> None:
        self.enabled = bool(settings.enabled)
        self.service_name = settings.service_name
        self.sample_rate = float(settings.sample_rate)
        self.export_console = bool(settings.export_console)
        self._otel_available = False
        self._tracer = None
        try:
            from importlib.metadata import version

            self.service_version = version("agent-cli")
        except Exception:
            self.service_version = "1.2.0"
        if not self.enabled:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider as SdkProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
                ConsoleSpanExporter,
                SimpleSpanProcessor,
            )

            resource = Resource.create(
                {"service.name": self.service_name, "service.version": self.service_version}
            )
            provider = SdkProvider(resource=resource)
            if self.export_console:
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            if settings.otlp_endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self.service_name)
            self._otel_available = True
        except ImportError:
            self._otel_available = False

    def should_sample(self) -> bool:
        if not self.enabled:
            return False
        if self.sample_rate >= 1.0:
            return True
        if self.sample_rate <= 0.0:
            return False
        return random.random() <= self.sample_rate

    def trace_context(self) -> dict[str, str]:
        ctx = dict(self._current_trace)
        if ctx:
            ctx["service.name"] = self.service_name
            ctx["service.version"] = self.service_version
        return ctx

    def _new_ids(self) -> tuple[str, str]:
        return secrets.token_hex(16), secrets.token_hex(8)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Any]:
        if not self.enabled or not self.should_sample():
            yield NoopSpan()
            return

        if self._otel_available and self._tracer is not None:
            with self._tracer.start_as_current_span(name) as otel_span:
                for key, value in attributes.items():
                    if value is not None:
                        otel_span.set_attribute(key, str(value))
                try:
                    from opentelemetry import trace

                    span_ctx = trace.get_current_span().get_span_context()
                    if span_ctx.is_valid:
                        self._current_trace = {
                            "trace_id": format(span_ctx.trace_id, "032x"),
                            "span_id": format(span_ctx.span_id, "016x"),
                        }
                except Exception:
                    pass
                try:
                    yield otel_span
                finally:
                    if not self._span_stack:
                        self._current_trace = {}
            return

        trace_id, span_id = self._new_ids()
        if not self._current_trace:
            self._current_trace = {"trace_id": trace_id, "span_id": span_id}
        else:
            self._current_trace = {
                "trace_id": self._current_trace.get("trace_id", trace_id),
                "span_id": span_id,
            }
        self._span_stack.append(span_id)
        record = SpanRecord(
            name=name,
            attributes={k: v for k, v in attributes.items() if v is not None},
            trace_id=self._current_trace.get("trace_id", trace_id),
            span_id=span_id,
        )
        start = time.monotonic()
        try:
            yield record
        except Exception:
            record.status = "error"
            raise
        finally:
            record.attributes["duration_ms"] = int((time.monotonic() - start) * 1000)
            self._memory.export(record)
            if self.export_console:
                print(f"[trace] {name} {record.attributes}", flush=True)
            if self._span_stack:
                self._span_stack.pop()
            if not self._span_stack:
                self._current_trace = {}
            elif len(self._span_stack) >= 1:
                self._current_trace["span_id"] = self._span_stack[-1]


def init_telemetry(config) -> TracerProvider:
    provider = TracerProvider.global_provider()
    provider.configure(config.telemetry)
    metrics = __import__("agent.metrics", fromlist=["MetricsCollector"]).MetricsCollector
    if config.telemetry.export_runtime_metrics:
        metrics.global_collector().set_histogram_buckets(config.telemetry.histogram_buckets_sec)
    return provider


def trace_span(name: str, **attributes: Any):
    return TracerProvider.global_provider().span(name, **attributes)


def trace_function(name: str, **default_attrs: Any):
    def decorator(fn):
        def wrapper(*args: Any, **kwargs: Any):
            attrs = dict(default_attrs)
            for key in ("thread_id", "turn_id", "tool", "backend", "mode"):
                if key in kwargs and kwargs[key] is not None:
                    attrs[key] = kwargs[key]
            with trace_span(name, **attrs):
                return fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def get_trace_context() -> dict[str, str]:
    return TracerProvider.global_provider().trace_context()


def memory_exporter() -> InMemorySpanExporter:
    return TracerProvider.global_provider()._memory
