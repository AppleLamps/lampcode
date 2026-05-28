"""Metrics, policy, sync, and webhook admin routes."""
from __future__ import annotations

import json
from pathlib import Path

from agent.config import Config
from agent.execution.sync.service import resolve_sync_path
from agent.metrics import MetricsCollector
from agent.serve.http_response import HttpResponseMixin


class AdminRoutesMixin(HttpResponseMixin):
    def _sync_resolve(self) -> None:
        if not self._get_ctx().settings.enable_control:
            self._error(403, "Control disabled")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "Invalid JSON")
            return
        rel_path = data.get("path", "")
        strategy = data.get("strategy", "local-wins")
        if not rel_path:
            self._error(400, "path required")
            return
        cwd = data.get("cwd")
        thread_id = data.get("thread_id")
        config = Config.resolve(cwd=Path(cwd) if cwd else None)
        config.execution.backend = "ssh"
        config.execution.ssh.sync_enabled = True
        result = resolve_sync_path(config, rel_path, strategy, thread_id=thread_id)
        status = 200 if result.get("ok") else 400
        self._json_response(result, status=status)

    def _metrics_response(self) -> None:
        snap = MetricsCollector.global_collector().snapshot()
        self._json_response(
            {
                "counters": snap.counters,
                "labeled_counters": snap.labeled_counters,
                "gauges": snap.gauges,
                "histograms": snap.histograms,
                "labeled_histograms": snap.labeled_histograms,
            }
        )

    def _prometheus_metrics_response(self) -> None:
        body = MetricsCollector.global_collector().to_prometheus().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
