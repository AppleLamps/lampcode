"""IDE HTTP routes for agent serve."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlparse

from agent.serve.auth import extract_session_token
from agent.serve.http_response import HttpResponseMixin
from agent.serve.request_limits import handle_body_error, read_limited_body
from agent.serve.ide import (
    IdeError,
    file_diff_from_thread,
    list_tree,
    read_file,
    resolve_thread_cwd,
    write_file_atomic,
)
from agent.metrics import MetricsCollector


class IdeRoutesMixin(HttpResponseMixin):
    def _ide_get(self, path: str, parsed) -> None:
        ctx = self._get_ctx()
        ide = ctx.settings.ide
        qs = parse_qs(parsed.query)
        thread_id = (qs.get("thread_id") or [None])[0]
        rel_path = (qs.get("path") or ["."])[0]
        if not thread_id:
            self._ide_metric("tree", "missing_thread")
            self._error(400, "thread_id required")
            return
        try:
            thread, cwd = resolve_thread_cwd(ctx.store, thread_id)
        except IdeError as exc:
            self._ide_metric(path.split("/")[-1], "error")
            self._error(exc.status, str(exc))
            return
        try:
            if path == "/ide/tree":
                data = list_tree(
                    cwd,
                    rel_path,
                    max_depth=ide.max_tree_depth,
                    max_entries=ide.max_tree_entries,
                )
                data["thread_id"] = thread.id
                data["cwd"] = str(cwd)
                self._ide_metric("tree", "ok")
                self._json_response(data)
            elif path == "/ide/file":
                data = read_file(cwd, rel_path, max_bytes=ide.max_file_bytes)
                self._ide_metric("file", "ok")
                self._json_response(data)
            elif path == "/ide/diff":
                data = file_diff_from_thread(thread, rel_path)
                self._ide_metric("diff", "ok")
                self._json_response(data)
            elif path == "/ide/history":
                from agent.serve.ide import compute_diff_gutter, file_history_from_thread

                history = file_history_from_thread(thread, rel_path)
                gutter = {"enabled": False}
                if ide.show_diff_gutter and history:
                    try:
                        current = read_file(cwd, rel_path, max_bytes=ide.max_file_bytes)
                        gutter = compute_diff_gutter(current.get("content", ""), history)
                    except IdeError:
                        gutter = {"enabled": False}
                self._ide_metric("history", "ok")
                self._json_response(
                    {
                        "path": rel_path,
                        "history": history,
                        "gutter": gutter,
                        "show_diff_gutter": ide.show_diff_gutter,
                    }
                )
            elif path == "/ide/tabs/state":
                from agent.metrics import MetricsCollector
                from agent.serve.ide import enforce_tab_limit, tabs_state_snapshot

                raw_tabs = (qs.get("tabs") or [""])[0]
                active = (qs.get("active") or [None])[0]
                tabs = [t for t in raw_tabs.split(",") if t] if raw_tabs else []
                trimmed, was_trimmed = enforce_tab_limit(tabs, max_tabs=ide.max_open_tabs)
                if trimmed:
                    MetricsCollector.global_collector().set_gauge("agent_ide_tabs_open", len(trimmed))
                self._json_response(
                    {
                        **tabs_state_snapshot(trimmed, active),
                        "max_open_tabs": ide.max_open_tabs,
                        "trimmed": was_trimmed,
                    }
                )
            elif path == "/ide/diagnostics":
                from agent.serve.ide import safe_resolve
                from agent.serve.ide_diagnostics import run_diagnostics

                if not ide.diagnostics.enabled:
                    self._json_response({"items": []})
                    return
                try:
                    file_path = safe_resolve(cwd, rel_path)
                except IdeError as exc:
                    self._error(exc.status, str(exc))
                    return
                items = run_diagnostics(file_path, settings=ide.diagnostics)
                self._ide_metric("diagnostics", "ok")
                self._json_response({"items": items, "path": rel_path})
            elif path == "/ide/completions":
                from agent.ide_lsp import fetch_completions, probe_python_lsp

                qs = parse_qs(parsed.query)
                rel_path = (qs.get("path") or [""])[0]
                line = int((qs.get("line") or ["1"])[0])
                col = int((qs.get("col") or ["0"])[0])
                probe = probe_python_lsp()
                items = fetch_completions(
                    path=rel_path, line=line, col=col, cwd=ctx.settings.cwd
                )
                self._json_response(
                    {"items": items, "path": rel_path, "lsp_available": probe.available, "server": probe.server}
                )
            else:
                self._error(404, "Not found")
        except IdeError as exc:
            self._ide_metric(path.split("/")[-1], "error")
            self._error(exc.status, str(exc))

    def _ide_put_file(self, parsed) -> None:
        ctx = self._get_ctx()
        ide = ctx.settings.ide
        qs = parse_qs(parsed.query)
        thread_id = (qs.get("thread_id") or [None])[0]
        rel_path = (qs.get("path") or [None])[0]
        if not thread_id or not rel_path:
            self._ide_metric("file", "missing_params")
            self._error(400, "thread_id and path required")
            return
        try:
            body = read_limited_body(
                self,
                max_bytes=min(ctx.settings.max_request_body_bytes, ide.max_file_bytes + 4096),
                default=b"",
            ).decode("utf-8")
        except Exception as exc:
            if handle_body_error(self, exc):
                return
            raise
        try:
            payload = json.loads(body) if body.startswith("{") else {"content": body}
        except json.JSONDecodeError:
            payload = {"content": body}
        content = payload.get("content", "")
        try:
            _, cwd = resolve_thread_cwd(ctx.store, thread_id)
            result = write_file_atomic(cwd, rel_path, content, max_bytes=ide.max_file_bytes)
            email = None
            if ctx.session_store and self.principal:
                sid = extract_session_token(dict(self.headers))
                if sid:
                    rec = ctx.session_store.get_session(sid)
                    email = rec.email if rec else None
            from agent.events import EventEmitter

            EventEmitter(lambda e: None).ide_file_write(
                thread_id,
                path=rel_path,
                bytes_written=result["bytes"],
                user=self.principal.name if self.principal else "unknown",
                role=self.principal.role if self.principal else "unknown",
                email=email,
            )
            self._ide_metric("file", "write_ok")
            self._json_response(result)
        except IdeError as exc:
            self._ide_metric("file", "error")
            self._error(exc.status, str(exc))

    def _ide_metric(self, route: str, result: str) -> None:
        MetricsCollector.global_collector().inc_labeled(
            "agent_ide_requests_total", f"{route}:{result}"
        )
