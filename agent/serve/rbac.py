from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Permission(str, Enum):
    READ = "read"
    SSE = "sse"
    START_TURN = "start_turn"
    APPROVE = "approve"
    CANCEL = "cancel"
    SYNC_RESOLVE = "sync_resolve"
    MANAGE_USERS = "manage_users"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "viewer": {Permission.READ, Permission.SSE},
    "operator": {
        Permission.READ,
        Permission.SSE,
        Permission.START_TURN,
        Permission.APPROVE,
        Permission.CANCEL,
        Permission.SYNC_RESOLVE,
    },
    "admin": set(Permission),
}


@dataclass
class AuthPrincipal:
    name: str
    role: str
    auth_method: str = "bearer"  # bearer | session | legacy

    def has_permission(self, perm: Permission) -> bool:
        allowed = ROLE_PERMISSIONS.get(self.role, ROLE_PERMISSIONS["viewer"])
        return perm in allowed


def hash_token(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_token_hash(token: str, stored: str) -> bool:
    if stored.startswith("sha256:"):
        return hash_token(token) == stored
    return hash_token(token) == f"sha256:{stored}"


def resolve_principal_from_token(
    token: str,
    *,
    rbac_enabled: bool,
    rbac_users: list[Any],
    legacy_auth_token: str,
    default_role: str = "viewer",
) -> AuthPrincipal | None:
    if not token:
        return None
    if rbac_enabled:
        for user in rbac_users:
            if verify_token_hash(token, user.token_hash):
                return AuthPrincipal(name=user.name, role=user.role, auth_method="bearer")
        if legacy_auth_token and token == legacy_auth_token:
            return AuthPrincipal(name="admin", role="admin", auth_method="legacy")
        return None
    if legacy_auth_token:
        if token == legacy_auth_token:
            return AuthPrincipal(name="legacy", role="admin", auth_method="legacy")
        return None
    return AuthPrincipal(name="anonymous", role="admin", auth_method="none")


def permission_for_route(method: str, path: str) -> Permission | None:
    clean = path.split("?")[0].rstrip("/") or "/"
    if clean in ("/auth/login", "/login"):
        return None
    if method == "GET":
        if clean.endswith("/events"):
            return Permission.SSE
        if clean in ("/", "/threads", "/metrics", "/metrics/prometheus") or clean.startswith(
            ("/threads/", "/runs/")
        ):
            return Permission.READ
    if method == "POST":
        if clean.endswith("/run"):
            return Permission.START_TURN
        if clean.startswith("/approvals/"):
            return Permission.APPROVE
        if clean.endswith("/cancel"):
            return Permission.CANCEL
        if clean == "/sync/resolve":
            return Permission.SYNC_RESOLVE
    return Permission.READ
