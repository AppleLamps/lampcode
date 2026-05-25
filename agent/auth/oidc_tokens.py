from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


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
    stored_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OidcTokenRecord:
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
            stored_at=float(data.get("stored_at", time.time())),
        )

    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() >= self.expires_at


class OidcTokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_oidc_auth_path()

    def save(self, record: OidcTokenRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "tokens": record.to_dict()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self) -> OidcTokenRecord | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return OidcTokenRecord.from_dict(data.get("tokens", data))
        except (json.JSONDecodeError, TypeError):
            return None

    def clear(self) -> None:
        if self.path.is_file():
            self.path.unlink()

    @staticmethod
    def keyring_available() -> bool:
        try:
            import keyring  # noqa: F401

            return True
        except ImportError:
            return False
