"""Coalesce high-frequency agent.delta events before TUI sync."""

from __future__ import annotations

# ~30fps cap for live assistant text updates
STREAM_SYNC_INTERVAL_SEC = 0.033
