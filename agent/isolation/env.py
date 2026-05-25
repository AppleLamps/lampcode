from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable

NETWORK_ENV_HINTS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def sanitize_environment(
    source: dict[str, str] | None = None,
    *,
    allowed_vars: Iterable[str],
    strip_env: bool = True,
    clear_network_env_hints: bool = True,
) -> tuple[dict[str, str], int]:
    """Return sanitized env and count of stripped variables."""
    source = source if source is not None else dict(os.environ)
    allowed = {v.upper() for v in allowed_vars}
    if not strip_env:
        env = dict(source)
        stripped = 0
    else:
        env = {}
        stripped = 0
        for key, value in source.items():
            upper = key.upper()
            if upper in allowed or key in allowed_vars:
                env[key] = value
            else:
                stripped += 1

    if clear_network_env_hints:
        for hint in NETWORK_ENV_HINTS:
            if hint in env:
                del env[hint]
                stripped += 1
            upper = hint.upper()
            for key in list(env):
                if key.upper() == upper:
                    del env[key]
                    stripped += 1

    return env, stripped
