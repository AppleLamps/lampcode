from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field


@dataclass
class MetricsSnapshot:
    counters: dict[str, int] = field(default_factory=dict)
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
            "sync_conflicts": 0,
            "sync_bytes_up": 0,
            "sync_bytes_down": 0,
        }
        self._gauges: dict[str, int] = {
            "active_turns": 0,
            "active_workers": 0,
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
                gauges=dict(self._gauges),
            )

    def to_json(self) -> str:
        snap = self.snapshot()
        return json.dumps(
            {"counters": snap.counters, "gauges": snap.gauges},
            indent=2,
        )

    def to_prometheus_lite(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        for k, v in snap.counters.items():
            lines.append(f"agent_cli_{k}_total {v}")
        for k, v in snap.gauges.items():
            lines.append(f"agent_cli_{k} {v}")
        return "\n".join(lines) + "\n"
