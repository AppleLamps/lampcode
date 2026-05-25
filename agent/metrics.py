from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field


@dataclass
class MetricsSnapshot:
    counters: dict[str, int] = field(default_factory=dict)
    labeled_counters: dict[str, dict[str, int]] = field(default_factory=dict)
    gauges: dict[str, int] = field(default_factory=dict)


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
        }
        self._labeled: dict[str, dict[str, int]] = {
            "agent_turns_total": {},
            "agent_tool_calls_total": {},
            "agent_sync_bytes_total": {},
            "agent_sync_conflicts_total": {},
            "agent_workers_total": {},
        }
        self._gauges: dict[str, int] = {
            "active_turns": 0,
            "active_workers": 0,
            "http_turns_active": 0,
        }

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

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def inc_labeled(self, metric: str, label: str, amount: int = 1) -> None:
        with self._lock:
            bucket = self._labeled.setdefault(metric, {})
            bucket[label] = bucket.get(label, 0) + amount

    def set_gauge(self, name: str, value: int) -> None:
        with self._lock:
            self._gauges[name] = value

    def adjust_gauge(self, name: str, delta: int) -> None:
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0) + delta

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                counters=dict(self._counters),
                labeled_counters={k: dict(v) for k, v in self._labeled.items()},
                gauges=dict(self._gauges),
            )

    def to_json(self) -> str:
        snap = self.snapshot()
        return json.dumps(
            {
                "counters": snap.counters,
                "labeled_counters": snap.labeled_counters,
                "gauges": snap.gauges,
            },
            indent=2,
        )

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
                elif metric == "agent_workers_total":
                    lines.append(f'{metric}{{status="{label}"}} {value}')
                else:
                    lines.append(f'{metric}{{label="{label}"}} {value}')
        for k, v in snap.gauges.items():
            prom = k if k.startswith("agent_") else f"agent_{k}"
            lines.append(f"# TYPE {prom} gauge")
            lines.append(f"{prom} {v}")
        return "\n".join(lines) + "\n"

    def to_prometheus_lite(self) -> str:
        return self.to_prometheus()
