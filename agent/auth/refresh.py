from __future__ import annotations

import time
from typing import Any

from agent.auth.oidc_tokens import OidcTokenRecord, OidcTokenStore
from agent.metrics import MetricsCollector
from agent.serve.oidc import OidcClient, ServeOidcSettings


class RefreshError(Exception):
    pass


def needs_refresh(record: OidcTokenRecord, *, skew_sec: int = 300) -> bool:
    if not record.refresh_token:
        return record.is_expired()
    if record.expires_at <= 0:
        return True
    return time.time() >= (record.expires_at - skew_sec)


def refresh_tokens(
    store: OidcTokenStore,
    oidc_settings: ServeOidcSettings,
    *,
    force: bool = False,
    http_client: Any = None,
) -> OidcTokenRecord:
    record = store.load()
    if record is None:
        raise RefreshError("No stored tokens — run agent auth login --device")
    if not record.refresh_token:
        raise RefreshError("No refresh token available")
    skew = oidc_settings.refresh_skew_sec if hasattr(oidc_settings, "refresh_skew_sec") else 300
    if not force and not needs_refresh(record, skew_sec=skew):
        return record

    client = OidcClient(oidc_settings)
    try:
        token_data = client.refresh_token(record.refresh_token, http_client=http_client)
    except Exception as exc:
        MetricsCollector.global_collector().inc_labeled("agent_auth_refresh_total", "error")
        store.clear()
        raise RefreshError(f"Refresh failed — re-login required: {exc}") from exc

    new_refresh = str(token_data.get("refresh_token", record.refresh_token))
    if oidc_settings.refresh_rotation and token_data.get("refresh_token"):
        new_refresh = str(token_data["refresh_token"])

    expires_in = float(token_data.get("expires_in", 3600))
    updated = OidcTokenRecord(
        issuer_url=record.issuer_url,
        client_id=record.client_id,
        access_token=str(token_data.get("access_token", "")),
        refresh_token=new_refresh,
        id_token=str(token_data.get("id_token", record.id_token)),
        expires_at=time.time() + expires_in,
        subject=record.subject,
        email=record.email,
        role=record.role,
        groups=list(record.groups),
        scopes=list(record.scopes),
    )
    if token_data.get("id_token"):
        claims = client.claims_from_token_response(token_data)
        rm = oidc_settings.role_mapping
        updated.subject = str(claims.get("sub", updated.subject))
        updated.email = str(claims.get(rm.claim_email_key, updated.email))
        updated.role = client.map_role(claims)

    store.save(updated)
    MetricsCollector.global_collector().inc_labeled("agent_auth_refresh_total", "success")
    return updated


def ensure_fresh_tokens(
    store: OidcTokenStore,
    oidc_settings: ServeOidcSettings | None,
    *,
    http_client: Any = None,
) -> OidcTokenRecord | None:
    if oidc_settings is None or not getattr(oidc_settings, "refresh_rotation", True):
        return store.load()
    record = store.load()
    if record is None:
        return None
    if needs_refresh(record, skew_sec=getattr(oidc_settings, "refresh_skew_sec", 300)):
        return refresh_tokens(store, oidc_settings, http_client=http_client)
    return record
