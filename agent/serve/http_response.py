"""HTTP response helpers for agent serve."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

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
            if hasattr(ctx.store, "find_matches_by_prefix"):
                matches = ctx.store.find_matches_by_prefix(thread_id, meta_only=True)
            else:
                matches = [
                    t
                    for t in ctx.store.list_thread_meta()
                    if t.id.startswith(thread_id) or t.id == thread_id
                ]
            if len(matches) == 1:
                return ctx.store.load_thread(matches[0].id)
            raise

    def _cors_origin(self) -> str | None:
        ctx = self._get_ctx()
        if not ctx.settings.cors:
            return None
        origin = self.headers.get("Origin") or self.headers.get("origin")
        if not origin:
            return None
        allowed = set(ctx.settings.cors_allowed_origins or [])
        session_auth = any(x in (ctx.settings.auth_mode or "").lower() for x in ("session", "oidc"))
        if "*" in allowed and not session_auth:
            return origin
        if origin in allowed:
            return origin
        return None

    def _apply_cors_headers(self) -> None:
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._apply_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._apply_cors_headers()
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


def _redact_item(item: Any) -> dict[str, Any]:
    data = item.model_dump()
    item_type = data.get("type")
    if item_type in {"agentMessage", "userMessage"}:
        text = data.pop("text", None)
        data["text_preview"] = (text[:160] + "...") if isinstance(text, str) and len(text) > 160 else text
        data["text_redacted"] = True
    if item_type == "commandExecution":
        if data.get("output") is not None:
            data["output_chars"] = data.get("output_chars") or len(str(data.get("output") or ""))
        data.pop("output", None)
        data.pop("tool_arguments", None)
        data["output_redacted"] = True
    if item_type == "fileChange":
        data.pop("content", None)
        data.pop("diff_snippet", None)
        data.pop("tool_arguments", None)
        data["content_redacted"] = True
    if item_type == "mcpToolCall":
        if data.get("output") is not None:
            data["output_chars"] = data.get("output_chars") or len(str(data.get("output") or ""))
        data.pop("output", None)
        data.pop("arguments", None)
        data["output_redacted"] = True
    return data


def thread_to_redacted_dict(thread: Thread, *, include_items: bool = True) -> dict[str, Any]:
    turns = []
    for turn in thread.turns:
        turn_data: dict[str, Any] = {"id": turn.id, "status": turn.status}
        if include_items:
            turn_data["items"] = [_redact_item(item) for item in turn.items]
        else:
            turn_data["item_count"] = len(turn.items)
        turns.append(turn_data)
    return {
        "id": thread.id,
        "cwd": thread.cwd,
        "model": thread.model,
        "title": thread.title,
        "forked_from": thread.forked_from,
        "active": ActiveTurnRegistry.global_registry().is_active(thread.id),
        "turns": turns,
        "redacted": True,
    }


def wants_full_thread_response(handler) -> bool:
    parsed = urlparse(getattr(handler, "path", ""))
    qs = parse_qs(parsed.query)
    if qs.get("full", [""])[0].lower() not in ("1", "true", "yes"):
        return False
    principal = getattr(handler, "principal", None)
    return getattr(principal, "role", "admin") == "admin"
