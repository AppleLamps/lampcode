from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent.metrics import MetricsCollector
from agent.settings import SwarmBudgetSettings


BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "off": {"enabled": False},
    "standard": {
        "enabled": True,
        "max_wall_clock_sec": 3600,
        "max_supervisor_tool_calls": 200,
        "max_workers_spawned": 20,
        "max_openrouter_input_tokens": 500_000,
        "max_openrouter_output_tokens": 200_000,
        "max_estimated_cost_usd": 25.0,
        "on_budget_exceeded": "kill",
    },
    "strict": {
        "enabled": True,
        "max_wall_clock_sec": 900,
        "max_supervisor_tool_calls": 50,
        "max_workers_spawned": 5,
        "max_openrouter_input_tokens": 100_000,
        "max_openrouter_output_tokens": 50_000,
        "max_estimated_cost_usd": 5.0,
        "on_budget_exceeded": "kill",
    },
}


def profile_settings(name: str, base: SwarmBudgetSettings | None = None) -> SwarmBudgetSettings:
    from agent.settings import SwarmBudgetSettings as SBS

    raw = BUILTIN_PROFILES.get(name.lower())
    if not raw:
        return base or SBS()
    merged = SBS()
    if base:
        merged = base
    for k, v in raw.items():
        if hasattr(merged, k):
            setattr(merged, k, v)
    return merged


@dataclass
class BudgetSnapshot:
    wall_clock_sec: float = 0.0
    supervisor_tool_calls: int = 0
    workers_spawned: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    exceeded: bool = False
    exceeded_metric: str = ""
    last_limit: float = 0.0
    last_observed: float = 0.0
    warn_once_used: bool = False


class SwarmBudgetTracker:
    def __init__(
        self,
        settings: SwarmBudgetSettings,
        *,
        model: str = "",
        started_at: float | None = None,
    ) -> None:
        self.settings = settings
        self.model = model
        self._started_at = started_at or time.monotonic()
        self.snapshot = BudgetSnapshot()

    def tick_wall_clock(self) -> str | None:
        if not self.settings.enabled:
            return None
        elapsed = time.monotonic() - self._started_at
        self.snapshot.wall_clock_sec = elapsed
        if self.settings.max_wall_clock_sec > 0 and elapsed > self.settings.max_wall_clock_sec:
            return self._exceed("wall_clock_sec", self.settings.max_wall_clock_sec, elapsed)
        return None

    def record_tool_call(self) -> str | None:
        if not self.settings.enabled:
            return None
        self.snapshot.supervisor_tool_calls += 1
        if (
            self.settings.max_supervisor_tool_calls > 0
            and self.snapshot.supervisor_tool_calls >= self.settings.max_supervisor_tool_calls
        ):
            return self._exceed(
                "supervisor_tool_calls",
                self.settings.max_supervisor_tool_calls,
                self.snapshot.supervisor_tool_calls,
            )
        return None

    def record_worker_spawn(self, count: int = 1) -> str | None:
        if not self.settings.enabled:
            return None
        self.snapshot.workers_spawned += count
        if (
            self.settings.max_workers_spawned > 0
            and self.snapshot.workers_spawned >= self.settings.max_workers_spawned
        ):
            return self._exceed(
                "workers_spawned",
                self.settings.max_workers_spawned,
                self.snapshot.workers_spawned,
            )
        return None

    def record_usage(self, usage: dict[str, Any] | None) -> str | None:
        if not self.settings.enabled or not usage:
            return None
        inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self.snapshot.input_tokens += inp
        self.snapshot.output_tokens += out
        self._update_cost(inp, out)
        if (
            self.settings.max_openrouter_input_tokens > 0
            and self.snapshot.input_tokens >= self.settings.max_openrouter_input_tokens
        ):
            return self._exceed(
                "input_tokens",
                self.settings.max_openrouter_input_tokens,
                self.snapshot.input_tokens,
            )
        if (
            self.settings.max_openrouter_output_tokens > 0
            and self.snapshot.output_tokens >= self.settings.max_openrouter_output_tokens
        ):
            return self._exceed(
                "output_tokens",
                self.settings.max_openrouter_output_tokens,
                self.snapshot.output_tokens,
            )
        if (
            self.settings.max_estimated_cost_usd > 0
            and self.snapshot.estimated_cost_usd >= self.settings.max_estimated_cost_usd
        ):
            return self._exceed(
                "estimated_cost_usd",
                self.settings.max_estimated_cost_usd,
                self.snapshot.estimated_cost_usd,
            )
        return None

    def _update_cost(self, inp: int, out: int) -> None:
        pricing = self.settings.pricing.get(self.model)
        if not pricing:
            return
        self.snapshot.estimated_cost_usd += (inp / 1_000_000) * pricing.input_per_million
        self.snapshot.estimated_cost_usd += (out / 1_000_000) * pricing.output_per_million

    def _exceed(self, metric: str, limit: float, observed: float) -> str | None:
        self.snapshot.last_limit = limit
        self.snapshot.last_observed = observed
        mode = (self.settings.on_budget_exceeded or "kill").lower()
        if mode == "warn" and not self.snapshot.warn_once_used:
            self.snapshot.warn_once_used = True
            MetricsCollector.global_collector().inc_labeled("agent_swarm_budget_exceeded_total", metric)
            return None
        self.snapshot.exceeded = True
        self.snapshot.exceeded_metric = metric
        MetricsCollector.global_collector().inc_labeled("agent_swarm_budget_exceeded_total", metric)
        return metric

    def should_kill(self) -> bool:
        if not self.settings.enabled or not self.snapshot.exceeded:
            return False
        return (self.settings.on_budget_exceeded or "kill").lower() == "kill"

    def to_dict(self) -> dict[str, Any]:
        return {
            "wall_clock_sec": round(self.snapshot.wall_clock_sec, 2),
            "supervisor_tool_calls": self.snapshot.supervisor_tool_calls,
            "workers_spawned": self.snapshot.workers_spawned,
            "input_tokens": self.snapshot.input_tokens,
            "output_tokens": self.snapshot.output_tokens,
            "estimated_cost_usd": round(self.snapshot.estimated_cost_usd, 4),
            "exceeded": self.snapshot.exceeded,
            "exceeded_metric": self.snapshot.exceeded_metric,
        }
