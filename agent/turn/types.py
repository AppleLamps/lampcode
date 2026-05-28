"""Turn tool-tracking item types."""
from __future__ import annotations

from agent.models import (
    CollabSpawnItem,
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    WebSearchItem,
)

TrackingItem = (
    CommandExecutionItem
    | FileChangeItem
    | McpToolCallItem
    | WebSearchItem
    | CollabSpawnItem
    | CollabWorkerItem
)
