from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent.serve.rbac import AuthPrincipal


@dataclass
class OidcRoleMapping:
    admin_groups: list[str] = field(default_factory=list)
    operator_groups: list[str] = field(default_factory=list)
    default_role: str = "viewer"
    claim_groups_key: str = "groups"
    claim_email_key: str = "email"


@dataclass
class ServeOidcSettings:
    enabled: bool = False
    issuer_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "https://127.0.0.1:8765/auth/oidc/callback"
    scopes: list[str] = field(default_factory=lambda: ["openid", "profile", "email"])
    pkce: bool = True
    session_ttl_sec: int = 28800
    role_mapping: OidcRoleMapping = field(default_factory=OidcRoleMapping)
    device_code_enabled: bool = False
    device_client_id: str = ""
    refresh_rotation: bool = True
    refresh_skew_sec: int = 300
    prefer_keyring: bool = True


@dataclass
class OidcDeviceFlow:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int
    created_at: float
    client_id: str


@dataclass
class OidcState:
    state: str
    nonce: str
    code_verifier: str
    created_at: float


class OidcClient:
    def __init__(self, settings: ServeOidcSettings) -> None:
        self.settings = settings
        self._pending: dict[str, OidcState] = {}
        self._device_pending: dict[str, OidcDeviceFlow] = {}
        self._discovery: dict[str, Any] | None = None

    def _generate_pkce(self) -> tuple[str, str]:
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        return verifier, challenge

    def start_login(self) -> tuple[str, OidcState]:
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = self._generate_pkce()
        rec = OidcState(state=state, nonce=nonce, code_verifier=verifier, created_at=time.time())
        self._pending[state] = rec
        params = {
            "client_id": self.settings.client_id,
            "response_type": "code",
            "scope": " ".join(self.settings.scopes),
            "redirect_uri": self.settings.redirect_uri,
            "state": state,
            "nonce": nonce,
        }
        if self.settings.pkce:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        auth_endpoint = self._auth_endpoint()
        url = f"{auth_endpoint}?{urllib.parse.urlencode(params)}"
        return url, rec

    def _auth_endpoint(self) -> str:
        issuer = self.settings.issuer_url.rstrip("/")
        return f"{issuer}/authorize"

    def _token_endpoint(self) -> str:
        issuer = self.settings.issuer_url.rstrip("/")
        return f"{issuer}/token"

    def validate_state(self, state: str) -> OidcState | None:
        rec = self._pending.pop(state, None)
        if rec is None:
            return None
        if time.time() - rec.created_at > 600:
            return None
        return rec

    def exchange_code(
        self,
        code: str,
        oidc_state: OidcState,
        *,
        http_client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "client_id": self.settings.client_id,
        }
        if self.settings.pkce:
            data["code_verifier"] = oidc_state.code_verifier
        if self.settings.client_secret:
            data["client_secret"] = self.settings.client_secret
        client = http_client or httpx.Client(timeout=30)
        own_client = http_client is None
        try:
            resp = client.post(self._token_endpoint(), data=data)
            resp.raise_for_status()
            token_data = resp.json()
        finally:
            if own_client:
                client.close()
        id_token = token_data.get("id_token", "")
        claims = _decode_jwt_payload(id_token) if id_token else {}
        if oidc_state.nonce and claims.get("nonce") != oidc_state.nonce:
            raise ValueError("nonce mismatch")
        return claims

    def map_role(self, claims: dict[str, Any]) -> str:
        mapping = self.settings.role_mapping
        groups = claims.get(mapping.claim_groups_key, [])
        if isinstance(groups, str):
            groups = [groups]
        groups_set = {str(g).lower() for g in groups}
        for g in mapping.admin_groups:
            if g.lower() in groups_set:
                return "admin"
        for g in mapping.operator_groups:
            if g.lower() in groups_set:
                return "operator"
        return mapping.default_role

    def principal_from_claims(self, claims: dict[str, Any]) -> AuthPrincipal:
        mapping = self.settings.role_mapping
        email = str(claims.get(mapping.claim_email_key, claims.get("sub", "oidc-user")))
        name = str(claims.get("name", email))
        role = self.map_role(claims)
        return AuthPrincipal(name=name, role=role, auth_method="oidc")

    def device_client_id(self) -> str:
        return self.settings.device_client_id or self.settings.client_id

    def start_device_flow(
        self,
        *,
        http_client: httpx.Client | None = None,
    ) -> OidcDeviceFlow:
        client_id = self.device_client_id()
        data = {
            "client_id": client_id,
            "scope": " ".join(self.settings.scopes),
        }
        client = http_client or httpx.Client(timeout=30)
        own_client = http_client is None
        try:
            resp = client.post(self._device_endpoint(), data=data)
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if own_client:
                client.close()
        flow = OidcDeviceFlow(
            device_code=str(payload["device_code"]),
            user_code=str(payload["user_code"]),
            verification_uri=str(payload.get("verification_uri", "")),
            verification_uri_complete=str(payload.get("verification_uri_complete", "")),
            expires_in=int(payload.get("expires_in", 600)),
            interval=int(payload.get("interval", 5)),
            created_at=time.time(),
            client_id=client_id,
        )
        self._device_pending[flow.device_code] = flow
        return flow

    def poll_device_token(
        self,
        device_code: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Return (status, token_data). status: success | pending | expired | error."""
        flow = self._device_pending.get(device_code)
        if flow is None:
            return "error", None
        if time.time() - flow.created_at > flow.expires_in:
            self._device_pending.pop(device_code, None)
            return "expired", None
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": flow.client_id,
        }
        if self.settings.client_secret:
            data["client_secret"] = self.settings.client_secret
        client = http_client or httpx.Client(timeout=30)
        own_client = http_client is None
        try:
            resp = client.post(self._token_endpoint(), data=data)
            if resp.status_code == 400:
                body = resp.json()
                err = body.get("error", "")
                if err in ("authorization_pending", "slow_down"):
                    return "pending", None
                if err == "expired_token":
                    self._device_pending.pop(device_code, None)
                    return "expired", None
                return "error", body
            resp.raise_for_status()
            token_data = resp.json()
        finally:
            if own_client:
                client.close()
        self._device_pending.pop(device_code, None)
        return "success", token_data

    def claims_from_token_response(self, token_data: dict[str, Any]) -> dict[str, Any]:
        id_token = token_data.get("id_token", "")
        if id_token:
            return _decode_jwt_payload(id_token)
        return {}

    def refresh_token(
        self,
        refresh_token: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.settings.client_id,
        }
        if self.settings.client_secret:
            data["client_secret"] = self.settings.client_secret
        client = http_client or httpx.Client(timeout=30)
        own_client = http_client is None
        try:
            resp = client.post(self._token_endpoint(), data=data)
            if resp.status_code >= 400:
                raise ValueError(f"refresh failed: {resp.status_code}")
            return resp.json()
        finally:
            if own_client:
                client.close()

    def _device_endpoint(self) -> str:
        issuer = self.settings.issuer_url.rstrip("/")
        return f"{issuer}/device"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(payload + padding)
    return json.loads(raw.decode("utf-8"))


def oidc_mode_active(auth_mode: str) -> bool:
    mode = (auth_mode or "").lower()
    return "oidc" in mode
