from __future__ import annotations

import json
import subprocess
import threading
from typing import Any


class LspRpcError(Exception):
    pass


class JsonRpcProcess:
    """LSP JSON-RPC over stdio (Content-Length framing)."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: float = 30.0,
        max_diagnostic_uris: int = 256,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = env
        self._timeout = timeout_sec
        self._max_diagnostic_uris = max_diagnostic_uris
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[int, dict[str, Any]] = {}
        self._diagnostics_by_uri: dict[str, list[dict[str, Any]]] = {}
        self._stop = threading.Event()
        self._read_error: str | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self._cwd,
            env=self._env,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self._proc or not self._proc.stdin:
            raise LspRpcError("LSP process not started")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
            self._write_message(body)
        result = self._wait_response(req_id)
        if "error" in result:
            err = result["error"]
            raise LspRpcError(f"LSP {method}: {err.get('message', err)}")
        return result.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self._proc or not self._proc.stdin:
            raise LspRpcError("LSP process not started")
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self._lock:
            self._write_message(body)

    def _write_message(self, body: dict[str, Any]) -> None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(header + data)
        self._proc.stdin.flush()

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        stream = self._proc.stdout
        try:
            while not self._stop.is_set():
                headers: dict[str, str] = {}
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    line = line.decode("ascii", errors="replace").strip()
                    if not line:
                        break
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                payload = stream.read(length)
                if len(payload) < length:
                    return
                msg = json.loads(payload.decode("utf-8"))
                if "id" in msg and "method" not in msg:
                    with self._lock:
                        self._pending[int(msg["id"])] = msg
                elif "method" in msg and "id" not in msg:
                    self._record_notification(msg)
        except Exception as exc:
            self._read_error = str(exc)

    def _record_notification(self, msg: dict[str, Any]) -> None:
        if msg.get("method") != "textDocument/publishDiagnostics":
            return
        params = msg.get("params", {})
        uri = params.get("uri")
        if not isinstance(uri, str):
            return
        diagnostics = params.get("diagnostics", [])
        if not isinstance(diagnostics, list):
            diagnostics = []
        with self._lock:
            if uri in self._diagnostics_by_uri:
                self._diagnostics_by_uri.pop(uri)
            self._diagnostics_by_uri[uri] = diagnostics
            while len(self._diagnostics_by_uri) > self._max_diagnostic_uris:
                oldest = next(iter(self._diagnostics_by_uri))
                self._diagnostics_by_uri.pop(oldest)

    def diagnostics_for_uri(self, uri: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._diagnostics_by_uri.get(uri, []))

    def _wait_response(self, req_id: int) -> dict[str, Any]:
        import time

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if self._read_error:
                raise LspRpcError(self._read_error)
            with self._lock:
                if req_id in self._pending:
                    return self._pending.pop(req_id)
            if self._proc and self._proc.poll() is not None:
                raise LspRpcError(f"LSP process exited ({self._proc.returncode})")
            time.sleep(0.01)
        raise LspRpcError(f"LSP request timed out after {self._timeout}s")
