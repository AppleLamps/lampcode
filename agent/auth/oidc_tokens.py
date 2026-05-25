from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.auth.storage import AuthStorageSettings, create_storage, keyring_available, select_backend


def default_oidc_auth_path() -> Path:
    return Path.home() / ".agent-cli" / "auth" / "oidc.json"


@dataclass
class OidcTokenRecord:
    issuer_url: str
    client_id: str
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    expires_at: float = 0.0
    subject: str = ""
    email: str = ""
    role: str = "viewer"
    groups: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    stored_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OidcTokenRecord:
        scopes = data.get("scopes", [])
        return cls(
            issuer_url=str(data.get("issuer_url", "")),
            client_id=str(data.get("client_id", "")),
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            id_token=str(data.get("id_token", "")),
            expires_at=float(data.get("expires_at", 0)),
            subject=str(data.get("subject", "")),
            email=str(data.get("email", "")),
            role=str(data.get("role", "viewer")),
            groups=list(data.get("groups", [])),
            scopes=[str(s) for s in scopes] if isinstance(scopes, list) else [],
            stored_at=float(data.get("stored_at", time.time())),
        )

    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() >= self.expires_at

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "issuer_url": self.issuer_url,
            "client_id": self.client_id,
            "subject": self.subject,
            "email": self.email,
            "role": self.role,
            "expires_at": self.expires_at,
            "has_refresh_token": bool(self.refresh_token),
            "scopes": self.scopes,
        }


class OidcTokenStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        storage_settings: AuthStorageSettings | None = None,
    ) -> None:
        self.path = path or default_oidc_auth_path()
        settings = storage_settings or AuthStorageSettings()
        self._storage = create_storage(settings, file_path=self.path)
        self._last_backend = self._storage.backend_name

    @property
    def backend_name(self) -> str:
        return self._storage.backend_name

    @staticmethod
    def keyring_available() -> bool:
        return keyring_available()

    @staticmethod
    def preferred_backend(settings: AuthStorageSettings | None = None) -> str:
        return select_backend(settings or AuthStorageSettings())

    def save(self, record: OidcTokenRecord) -> None:
        record.stored_at = time.time()
        self._storage.save(record.to_dict())
        if self._storage.backend_name == "file" and not keyring_available():
            pass  # caller/doctor emits warning

    def load(self) -> OidcTokenRecord | None:
        data = self._storage.load()
        if not data:
            # backward compat: try file if keyring empty
            if self._storage.backend_name == "keyring":
                fb = create_storage(AuthStorageSettings(backend="file"), file_path=self.path)
                data = fb.load()
        if not data:
            return None
        return OidcTokenRecord.from_dict(data)

    def clear(self) -> None:
        self._storage.clear()
        if self._storage.backend_name == "keyring" and self.path.is_file():
            self.path.unlink(missing_ok=True)
