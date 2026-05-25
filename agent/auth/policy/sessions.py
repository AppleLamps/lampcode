from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def default_revocation_path() -> Path:
    return Path.home() / ".agent-cli" / "auth" / "revoked-sessions.json"


def _hash_session_id(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


@dataclass
class RevocationRecord:
    session_hash: str
    revoked_at: float
    reason: str = ""


class SessionRevocationRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_revocation_path()
        self._entries: dict[str, RevocationRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in data.get("revoked", []):
                h = str(item.get("session_hash", ""))
                if h:
                    self._entries[h] = RevocationRecord(
                        session_hash=h,
                        revoked_at=float(item.get("revoked_at", time.time())),
                        reason=str(item.get("reason", "")),
                    )
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "revoked": [
                {
                    "session_hash": r.session_hash,
                    "revoked_at": r.revoked_at,
                    "reason": r.reason,
                }
                for r in self._entries.values()
            ]
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def revoke(self, session_id: str, *, reason: str = "") -> None:
        h = _hash_session_id(session_id)
        self._entries[h] = RevocationRecord(session_hash=h, revoked_at=time.time(), reason=reason)
        self._persist()

    def is_revoked(self, session_id: str) -> bool:
        return _hash_session_id(session_id) in self._entries

    def revoke_all_except(
        self,
        keep_session_id: str | None = None,
        session_ids: list[str] | None = None,
        *,
        reason: str = "revoke-all",
    ) -> int:
        count = 0
        for sid in session_ids or []:
            if keep_session_id and sid == keep_session_id:
                continue
            self.revoke(sid, reason=reason)
            count += 1
        return count

    def mark_revoked_hashes(self, hashes: list[str], *, reason: str = "") -> int:
        n = 0
        for h in hashes:
            if h not in self._entries:
                self._entries[h] = RevocationRecord(
                    session_hash=h, revoked_at=time.time(), reason=reason
                )
                n += 1
        if n:
            self._persist()
        return n

    def list_revoked(self) -> list[dict[str, Any]]:
        return [
            {"session_hash": r.session_hash[:16] + "...", "revoked_at": r.revoked_at, "reason": r.reason}
            for r in self._entries.values()
        ]

    def clear(self) -> None:
        self._entries.clear()
        if self.path.is_file():
            self.path.unlink()
