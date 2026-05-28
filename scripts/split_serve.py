"""Split agent/serve/server.py into mixins. Run from agent-cli/."""
from __future__ import annotations

from pathlib import Path

ROOT = Path("agent/serve")
SRC = (ROOT / "server.py").read_text(encoding="utf-8")
lines = SRC.splitlines(keepends=True)


def slice_lines(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path.relative_to('agent')} ({len(text)} bytes)")


# http_response.py
write(
    ROOT / "http_response.py",
    '''"""HTTP response helpers for agent serve."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlparse

from agent.harness.active_turns import ActiveTurnRegistry
from agent.models import Thread
from agent.serve.server import ServeContext
from agent.store import ThreadStore


class HttpResponseMixin:
    """Shared JSON/HTML responses and thread loading."""

    ctx: ServeContext | None
    store: ThreadStore | None
    run_store: object | None
    principal: object = None

'''
    + slice_lines(75, 83)
    + slice_lines(1027, 1060)
    + "\n\n"
    + '''def thread_to_dict(thread: Thread) -> dict[str, Any]:
'''
    + slice_lines(1063, 1079).replace("def _thread_to_dict", "def thread_to_dict", 1),
)

# auth_routes.py
write(
    ROOT / "routes" / "auth_routes.py",
    '''"""Authentication and authorization routes for agent serve."""
from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent.serve.auth import authorize_request_v2, extract_bearer_token, extract_query_token, extract_session_token
from agent.serve.dashboard import render_login_html
from agent.serve.http_response import HttpResponseMixin
from agent.serve.oidc import oidc_mode_active
from agent.serve.policy_gate import enforce_login_policy, enforce_request_policy
from agent.auth.webhooks.revoke import revoke_for_event, verify_signature
from agent.auth.webhooks.oidc_events import parse_oidc_event


class AuthRoutesMixin(HttpResponseMixin):
'''
    + slice_lines(516, 807),
)

# ide_routes.py
write(
    ROOT / "routes" / "ide_routes.py",
    '''"""IDE HTTP routes for agent serve."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlparse

from agent.serve.http_response import HttpResponseMixin
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
'''
    + slice_lines(361, 514),
)

# thread_routes.py  
write(
    ROOT / "routes" / "thread_routes.py",
    '''"""Thread, turn, approval, and SSE routes for agent serve."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent.config import Config
from agent.export.html import export_thread_html
from agent.harness.active_turns import ActiveTurnRegistry
from agent.metrics import MetricsCollector
from agent.multi_agent.checkpoint import CheckpointStore
from agent.serve.approvals import ApprovalRegistry, map_api_decision
from agent.serve.http_response import HttpResponseMixin, thread_to_dict
from agent.serve.turn_runner import TurnRunner
from agent.execution.sync.service import resolve_sync_path


class ThreadRoutesMixin(HttpResponseMixin):
'''
    + slice_lines(816, 1005),
)

# admin_routes.py
write(
    ROOT / "routes" / "admin_routes.py",
    '''"""Metrics, policy, sync, and webhook admin routes."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

from agent.execution.sync.service import resolve_sync_path
from agent.serve.http_response import HttpResponseMixin


class AdminRoutesMixin(HttpResponseMixin):
'''
    + slice_lines(826, 849)
    + slice_lines(1007, 1025),
)

print("split_serve done — update server.py manually to use mixins")
