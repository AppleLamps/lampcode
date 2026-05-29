from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class RequestBodyTooLarge(Exception):
    limit: int
    actual: int | None = None

    def __str__(self) -> str:
        if self.actual is None:
            return f"Request body exceeds {self.limit} bytes"
        return f"Request body is {self.actual} bytes; limit is {self.limit} bytes"


@dataclass
class InvalidRequestBody(Exception):
    message: str
    status: int = 400

    def __str__(self) -> str:
        return self.message


def _content_length(handler) -> int:
    raw = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw)
    except (TypeError, ValueError):
        raise InvalidRequestBody("Invalid Content-Length")
    if length < 0:
        raise InvalidRequestBody("Invalid Content-Length")
    return length


def read_limited_body(handler, *, max_bytes: int, default: bytes = b"{}") -> bytes:
    if max_bytes <= 0:
        raise InvalidRequestBody("max_request_body_bytes must be positive", status=500)
    length = _content_length(handler)
    if length == 0:
        return default
    if length > max_bytes:
        raise RequestBodyTooLarge(limit=max_bytes, actual=length)
    return handler.rfile.read(length)


def read_limited_json(handler, *, max_bytes: int) -> dict[str, Any]:
    body = read_limited_body(handler, max_bytes=max_bytes, default=b"{}")
    try:
        parsed = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidRequestBody("Invalid JSON")
    if not isinstance(parsed, dict):
        raise InvalidRequestBody("JSON body must be an object")
    return parsed


def handle_body_error(handler, exc: Exception) -> bool:
    if isinstance(exc, RequestBodyTooLarge):
        handler._error(413, str(exc))
        return True
    if isinstance(exc, InvalidRequestBody):
        handler._error(exc.status, exc.message)
        return True
    return False