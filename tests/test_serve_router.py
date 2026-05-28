"""Smoke tests for serve router."""

from __future__ import annotations

from agent.serve.router import ServeRouter


def test_serve_router_exact_match() -> None:
    router = ServeRouter()
    router.register("GET", "/auth/me", "_auth_me")
    match = router.match("GET", "/auth/me")
    assert match is not None
    assert match.handler_name == "_auth_me"
    assert router.match("POST", "/auth/me") is None
