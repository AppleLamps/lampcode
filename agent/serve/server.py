from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent.config import Config
from agent.export.html import export_thread_html, render_index_html
from agent.models import Thread
from agent.recording.store import RunStore
from agent.store import ThreadStore


class AgentHttpHandler(BaseHTTPRequestHandler):
    store: ThreadStore
    run_store: RunStore

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        if path == "/":
            threads = self.store.list_threads()
            self._html_response(render_index_html(threads))
            return

        if path == "/threads":
            threads = self.store.list_threads()
            data = [
                {
                    "id": t.id,
                    "title": t.title,
                    "cwd": t.cwd,
                    "updated_at": t.updated_at,
                    "label": t.display_label(),
                }
                for t in threads
            ]
            self._json_response(data)
            return

        if path.startswith("/threads/"):
            thread_id = path.split("/threads/", 1)[1]
            suffix = ""
            if thread_id.endswith(".html"):
                thread_id = thread_id[: -len(".html")]
                suffix = ".html"
            try:
                thread = self._load_thread(thread_id)
            except FileNotFoundError:
                self._error(404, "Thread not found")
                return
            wants_html = suffix == ".html" or "text/html" in self.headers.get("Accept", "")
            if wants_html:
                cfg = Config.resolve(cwd=Path(thread.cwd))
                self._html_response(
                    export_thread_html(
                        thread,
                        sandbox=cfg.sandbox_mode.value,
                        backend=cfg.execution.backend,
                    )
                )
                return
            self._json_response(_thread_to_dict(thread))
            return

        if path.startswith("/runs/"):
            turn_id = path.split("/runs/", 1)[1]
            try:
                events = self.run_store.load_events(turn_id)
            except FileNotFoundError:
                self._error(404, "Run not found")
                return
            self._json_response([json.loads(e.to_json()) for e in events])
            return

        self._error(404, "Not found")

    def _load_thread(self, thread_id: str) -> Thread:
        try:
            return self.store.load_thread(thread_id)
        except FileNotFoundError:
            matches = [
                t
                for t in self.store.list_threads()
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


def _thread_to_dict(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "cwd": thread.cwd,
        "model": thread.model,
        "title": thread.title,
        "forked_from": thread.forked_from,
        "turns": [
            {
                "id": turn.id,
                "status": turn.status,
                "items": [item.model_dump() for item in turn.items],
            }
            for turn in thread.turns
        ],
    }


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    store = ThreadStore()
    run_store = RunStore()

    class Handler(AgentHttpHandler):
        pass

    Handler.store = store
    Handler.run_store = run_store

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"agent serve listening on http://{host}:{port}")
    print("  GET /              HTML thread list")
    print("  GET /threads       JSON thread list")
    print("  GET /threads/{id}  JSON transcript (append .html for HTML)")
    print("  GET /runs/{turn_id}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
