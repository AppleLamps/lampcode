from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from agent.serve.rbac import AuthPrincipal, Permission, permission_for_route, resolve_principal_from_token


@dataclass
class AuthResult:
    authorized: bool
    error: str | None = None
    principal: AuthPrincipal | None = None
    required_permission: Permission | None = None


def extract_bearer_token(headers: dict[str, str]) -> str | None:
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def extract_session_token(headers: dict[str, str]) -> str | None:
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.lower().startswith("session "):
        return auth[8:].strip()
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("agent_session="):
            return part.split("=", 1)[1].strip()
    return None


def extract_query_token(path: str) -> str | None:
    parsed = urlparse(path)
    qs = parse_qs(parsed.query)
    tokens = qs.get("token")
    if tokens:
        return tokens[0]
    sessions = qs.get("session")
    if sessions:
        return sessions[0]
    return None


def authorize_request(
    path: str,
    headers: dict[str, str],
    *,
    auth_token: str,
    public_paths: set[str] | None = None,
    auth_mode: str = "bearer",
    rbac_enabled: bool = False,
    rbac_users: list | None = None,
    default_role: str = "viewer",
    session_store=None,
    method: str = "GET",
) -> tuple[bool, str | None]:
    """Legacy API: Return (authorized, error_message)."""
    result = authorize_request_v2(
        path,
        headers,
        auth_token=auth_token,
        public_paths=public_paths,
        auth_mode=auth_mode,
        rbac_enabled=rbac_enabled,
        rbac_users=rbac_users or [],
        default_role=default_role,
        session_store=session_store,
        method=method,
    )
    return result.authorized, result.error


def authorize_request_v2(
    path: str,
    headers: dict[str, str],
    *,
    auth_token: str,
    public_paths: set[str] | None = None,
    auth_mode: str = "bearer",
    rbac_enabled: bool = False,
    rbac_users: list | None = None,
    default_role: str = "viewer",
    session_store=None,
    method: str = "GET",
) -> AuthResult:
    clean_path = path.split("?")[0].rstrip("/") or "/"
    if public_paths and clean_path in public_paths:
        return AuthResult(
            authorized=True,
            principal=AuthPrincipal(name="public", role="viewer", auth_method="public"),
        )

    required = permission_for_route(method, path)
    mode = (auth_mode or "bearer").lower()
    users = rbac_users or []

    principal: AuthPrincipal | None = None

    if mode in ("session", "both", "oidc", "oidc+both", "oidc+bearer") and session_store is not None:
        session_id = extract_session_token(headers)
        if session_id:
            principal = session_store.principal_from_session(session_id)

    if principal is None and mode in ("bearer", "both", "oidc+bearer", "oidc+both"):
        token = extract_bearer_token(headers) or extract_query_token(path)
        if token:
            principal = resolve_principal_from_token(
                token,
                rbac_enabled=rbac_enabled,
                rbac_users=users,
                legacy_auth_token=auth_token,
                default_role=default_role,
            )
            if principal is None and mode in ("session", "both", "oidc+both") and session_store is not None:
                principal = session_store.principal_from_session(token)

    if not auth_token and not rbac_enabled and "oidc" not in mode and mode in ("bearer",):
        principal = AuthPrincipal(name="anonymous", role="admin", auth_method="none")

    if principal is None:
        return AuthResult(authorized=False, error="Unauthorized", required_permission=required)

    if rbac_enabled and required is not None and not principal.has_permission(required):
        return AuthResult(
            authorized=False,
            error=f"Forbidden: role '{principal.role}' cannot {required.value}",
            principal=principal,
            required_permission=required,
        )

    return AuthResult(authorized=True, principal=principal, required_permission=required)
