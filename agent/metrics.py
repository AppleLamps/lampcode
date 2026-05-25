from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class MetricsSnapshot:
    counters: dict[str, int] = field(default_factory=dict)
    labeled_counters: dict[str, dict[str, int]] = field(default_factory=dict)
    gauges: dict[str, int] = field(default_factory=dict)
    histograms: dict[str, list[float]] = field(default_factory=dict)
    labeled_histograms: dict[str, dict[str, list[float]]] = field(default_factory=dict)


class MetricsCollector:
    _instance: MetricsCollector | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {
            "turns_started": 0,
            "turns_completed": 0,
            "workers_spawned": 0,
            "workers_failed": 0,
            "workers_retry_success": 0,
            "workers_retry_failed": 0,
            "sync_conflicts": 0,
            "sync_bytes_up": 0,
            "sync_bytes_down": 0,
            "http_turns_started": 0,
            "openrouter_retries": 0,
            "tool_calls": 0,
            "errors_total": 0,
        }
        self._labeled: dict[str, dict[str, int]] = {
            "agent_turns_total": {},
            "agent_tool_calls_total": {},
            "agent_sync_bytes_total": {},
            "agent_sync_conflicts_total": {},
            "agent_workers_total": {},
            "agent_workers_dag_nodes_total": {},
            "agent_errors_total": {},
        }
        self._gauges: dict[str, int] = {
            "active_turns": 0,
            "active_workers": 0,
            "http_turns_active": 0,
        }
        self._histograms: dict[str, list[float]] = {
            "agent_turn_duration_seconds": [],
            "agent_tool_duration_seconds": [],
            "agent_http_request_duration_seconds": [],
        }
        self._labeled_histograms: dict[str, dict[str, list[float]]] = {
            "agent_tool_duration_seconds": {},
            "agent_http_request_duration_seconds": {},
        }
        self._histogram_buckets = [0.1, 0.5, 1, 2, 5, 10, 30, 60, 120]

    @classmethod
    def global_collector(cls) -> MetricsCollector:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def set_histogram_buckets(self, buckets: list[float]) -> None:
        with self._lock:
            self._histogram_buckets = list(buckets)

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def inc_labeled(self, metric: str, label: str, amount: int = 1) -> None:
        with self._lock:
            bucket = self._labeled.setdefault(metric, {})
            bucket[label] = bucket.get(label, 0) + amount

    def inc_error(self, component: str) -> None:
        self.inc("errors_total")
        self.inc_labeled("agent_errors_total", component)

    def set_gauge(self, name: str, value: int) -> None:
        with self._lock:
            self._gauges[name] = value

    def adjust_gauge(self, name: str, delta: int) -> None:
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0) + delta

    def observe(self, metric: str, value_sec: float, *, label: str | None = None) -> None:
        with self._lock:
            if label:
                bucket = self._labeled_histograms.setdefault(metric, {})
                bucket.setdefault(label, []).append(value_sec)
            else:
                self._histograms.setdefault(metric, []).append(value_sec)

    @contextmanager
    def timed(self, metric: str, *, label: str | None = None):
        start = time.monotonic()
        try:
            yield
        finally:
            self.observe(metric, time.monotonic() - start, label=label)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                counters=dict(self._counters),
                labeled_counters={k: dict(v) for k, v in self._labeled.items()},
                gauges=dict(self._gauges),
                histograms={k: list(v) for k, v in self._histograms.items()},
                labeled_histograms={k: {lk: list(lv) for lk, lv in v.items()} for k, v in self._labeled_histograms.items()},
            )

    def to_json(self) -> str:
        snap = self.snapshot()
        return json.dumps(
            {
                "counters": snap.counters,
                "labeled_counters": snap.labeled_counters,
                "gauges": snap.gauges,
                "histograms": snap.histograms,
                "labeled_histograms": snap.labeled_histograms,
            },
            indent=2,
        )

    def _format_histogram(self, name: str, values: list[float], label: str | None = None) -> list[str]:
        lines: list[str] = []
        buckets = self._histogram_buckets
        counts = [0] * (len(buckets) + 1)
        total = 0
        total_sum = 0.0
        for v in values:
            total += 1
            total_sum += v
            placed = False
            for i, b in enumerate(buckets):
                if v <= b:
                    counts[i] += 1
                    placed = True
                    break
            if not placed:
                counts[-1] += 1
        label_str = f'{{path="{label}"}}' if label else ""
        label_str_tool = f'{{tool="{label}"}}' if label and "tool" in name else label_str
        if label and "http" in name:
            label_str_tool = f'{{path="{label}"}}'
        elif label and "tool" in name:
            label_str_tool = f'{{tool="{label}"}}'
        cum = 0
        for i, b in enumerate(buckets):
            cum += counts[i]
            lines.append(f"{name}_bucket{label_str_tool} {{le=\"{b}\"}} {cum}")
        cum += counts[-1]
        lines.append(f"{name}_bucket{label_str_tool} {{le=\"+Inf\"}} {cum}")
        lines.append(f"{name}_count{label_str_tool} {total}")
        lines.append(f"{name}_sum{label_str_tool} {total_sum}")
        return lines

    def to_prometheus(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        for k, v in snap.counters.items():
            prom_name = k if k.startswith("agent_") else f"agent_{k}_total"
            if not prom_name.endswith("_total"):
                prom_name = f"{prom_name}_total"
            lines.append(f"# TYPE {prom_name} counter")
            lines.append(f"{prom_name} {v}")
        for metric, labels in snap.labeled_counters.items():
            lines.append(f"# TYPE {metric} counter")
            for label, value in labels.items():
                if metric == "agent_sync_bytes_total":
                    lines.append(f'{metric}{{direction="{label}"}} {value}')
                elif metric == "agent_turns_total":
                    lines.append(f'{metric}{{status="{label}"}} {value}')
                elif metric == "agent_tool_calls_total":
                    lines.append(f'{metric}{{tool="{label}"}} {value}')
                elif metric == "agent_sync_conflicts_total":
                    lines.append(f'{metric}{{reason="{label}"}} {value}')
                elif metric == "agent_workers_dag_nodes_total":
                    lines.append(f'{metric}{{status="{label}"}} {value}')
                elif metric == "agent_workers_total":
                    lines.append(f'{metric}{{status="{label}"}} {value}')
                elif metric == "agent_errors_total":
                    lines.append(f'{metric}{{component="{label}"}} {value}')
                else:
                    lines.append(f'{metric}{{label="{label}"}} {value}')
        for k, v in snap.gauges.items():
            prom = k if k.startswith("agent_") else f"agent_{k}"
            lines.append(f"# TYPE {prom} gauge")
            lines.append(f"{prom} {v}")
        for metric, values in snap.histograms.items():
            lines.append(f"# TYPE {metric} histogram")
            lines.extend(self._format_histogram(metric, values))
        for metric, labels in snap.labeled_histograms.items():
            lines.append(f"# TYPE {metric} histogram")
            for label, values in labels.items():
                lines.extend(self._format_histogram(metric, values, label=label))
        return "\n".join(lines) + "\n"

    def to_prometheus_lite(self) -> str:
        return self.to_prometheus()
