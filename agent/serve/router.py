"""Simple path router for agent serve (method + exact/prefix match)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class RouteMatch:
    handler_name: str
    path_params: dict[str, str]


@dataclass
class _Route:
    method: str
    pattern: str
    handler_name: str
    prefix: bool = False


class ServeRouter:
    """Register routes and resolve handler method names on AgentHttpHandler."""

    def __init__(self) -> None:
        self._routes: list[_Route] = []

    def register(
        self,
        method: str,
        pattern: str,
        handler_name: str,
        *,
        prefix: bool = False,
    ) -> None:
        self._routes.append(
            _Route(method.upper(), pattern.rstrip("/") or "/", handler_name, prefix=prefix)
        )

    def match(self, method: str, path: str) -> RouteMatch | None:
        clean = unquote(urlparse(path).path.rstrip("/")) or "/"
        method = method.upper()
        for route in self._routes:
            if route.method != method:
                continue
            if route.prefix:
                if clean == route.pattern or clean.startswith(route.pattern + "/"):
                    return RouteMatch(route.handler_name, {})
            elif clean == route.pattern:
                return RouteMatch(route.handler_name, {})
        return None


def default_serve_router() -> ServeRouter:
    """Built-in route table for public auth endpoints (handler lives on mixins)."""
    router = ServeRouter()
    for path, name in (
        ("/auth/oidc/login", "_oidc_login_redirect"),
        ("/auth/oidc/callback", "_oidc_callback"),
        ("/login", "_serve_login_page"),
        ("/auth/login", "_auth_login"),
        ("/auth/logout", "_auth_logout"),
        ("/auth/oidc/device/start", "_oidc_device_start"),
        ("/auth/oidc/device/poll", "_oidc_device_poll"),
    ):
        method = "GET" if "login" in path and path != "/auth/login" and "device" not in path else "POST"
        if path in ("/auth/oidc/login", "/auth/oidc/callback", "/login"):
            method = "GET"
        router.register(method, path, name)
    return router
