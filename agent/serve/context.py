"""Serve runtime context shared by HTTP route mixins."""

from __future__ import annotations

from pathlib import Path

from agent.auth.policy.sessions import SessionRevocationRegistry
from agent.events import EventEmitter
from agent.recording.store import RunStore
from agent.serve.oidc import OidcClient
from agent.serve.sessions import SessionStore
from agent.settings import ServeSettings
from agent.store import ThreadStore


class ServeContext:
    def __init__(
        self,
        *,
        store: ThreadStore,
        run_store: RunStore,
        settings: ServeSettings,
        auth_token: str,
        session_store: SessionStore | None = None,
        rbac_users: list | None = None,
        oidc_client: OidcClient | None = None,
        revocation_registry: SessionRevocationRegistry | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self.store = store
        self.run_store = run_store
        self.settings = settings
        self.auth_token = auth_token
        self.session_store = session_store
        self.rbac_users = rbac_users or []
        self.oidc_client = oidc_client
        self.revocation_registry = revocation_registry or SessionRevocationRegistry()
        self.emitter = emitter or EventEmitter()
