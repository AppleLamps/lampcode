"""HTTP response helpers for agent serve."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlparse

from agent.harness.active_turns import ActiveTurnRegistry
from agent.models import Thread
from agent.recording.store import RunStore
from agent.serve.context import ServeContext
from agent.settings import ServeSettings
from agent.store import ThreadStore


class HttpResponseMixin:
    """Shared JSON/HTML responses and thread loading."""

    ctx: ServeContext | None
    store: ThreadStore | None
    run_store: object | None
    principal: object = None

    def _get_ctx(self) -> ServeContext:
        if self.ctx is not None:
            return self.ctx
        return ServeContext(
            store=self.store or ThreadStore(),
            run_store=self.run_store or RunStore(),
            settings=ServeSettings(),
            auth_token="",
        )

    def _load_thread(self, thread_id: str) -> Thread:
        ctx = self._get_ctx()
        try:
            return ctx.store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [
                t
                for t in ctx.store.list_threads()
                if t.id.startswith(thread_id) or t.id == thread_id
            ]
            if len(matches) == 1:
                return matches[0]
            raise

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self._get_ctx().settings.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json_response({"error": message}, status=status)


def thread_to_dict(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "cwd": thread.cwd,
        "model": thread.model,
        "title": thread.title,
        "forked_from": thread.forked_from,
        "active": ActiveTurnRegistry.global_registry().is_active(thread.id),
        "turns": [
            {
                "id": turn.id,
                "status": turn.status,
                "items": [item.model_dump() for item in turn.items],
            }
            for turn in thread.turns
        ],
    }
