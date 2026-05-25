from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.serve.rbac import AuthPrincipal, resolve_principal_from_token


@dataclass
class SessionRecord:
    session_id: str
    principal_name: str
    role: str
    created_at: float
    expires_at: float
    auth_method: str = "session"
    subject: str | None = None
    email: str | None = None
    groups: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)
    last_activity_at: float = 0.0


class SessionStore:
    _instance: SessionStore | None = None

    def __init__(self, *, ttl_sec: int = 28800, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._login_attempts: dict[str, list[float]] = {}
        self.ttl_sec = ttl_sec
        self.persist_path = persist_path
        self._load_persisted()

    @classmethod
    def global_store(cls, *, ttl_sec: int = 28800, persist_path: Path | None = None) -> SessionStore:
        if cls._instance is None:
            cls._instance = cls(ttl_sec=ttl_sec, persist_path=persist_path)
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def _load_persisted(self) -> None:
        if not self.persist_path or not self.persist_path.is_file():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            now = time.time()
            for item in data.get("sessions", []):
                if item.get("expires_at", 0) > now:
                    rec = SessionRecord(
                        session_id=item["session_id"],
                        principal_name=item["principal_name"],
                        role=item["role"],
                        created_at=item["created_at"],
                        expires_at=item["expires_at"],
                        auth_method=item.get("auth_method", "session"),
                        subject=item.get("subject"),
                        email=item.get("email"),
                        groups=list(item.get("groups", [])),
                        claims=dict(item.get("claims", {})),
                        last_activity_at=float(item.get("last_activity_at", item.get("created_at", 0))),
                    )
                    self._sessions[rec.session_id] = rec
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _persist(self) -> None:
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "principal_name": s.principal_name,
                        "role": s.role,
                        "created_at": s.created_at,
                        "expires_at": s.expires_at,
                        "auth_method": s.auth_method,
                        "subject": s.subject,
                        "email": s.email,
                        "groups": s.groups,
                        "claims": s.claims,
                        "last_activity_at": s.last_activity_at,
                    }
                    for s in self._sessions.values()
                    if s.expires_at > time.time()
                ]
            }
            self.persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if s.expires_at <= now]
        for sid in expired:
            del self._sessions[sid]

    def check_rate_limit(self, client_key: str, *, max_attempts: int = 5, window_sec: int = 60) -> bool:
        now = time.time()
        with self._lock:
            attempts = [t for t in self._login_attempts.get(client_key, []) if now - t < window_sec]
            self._login_attempts[client_key] = attempts
            return len(attempts) < max_attempts

    def record_failed_login(self, client_key: str) -> None:
        with self._lock:
            self._login_attempts.setdefault(client_key, []).append(time.time())

    def create_session(
        self,
        principal: AuthPrincipal,
        *,
        ttl_sec: int | None = None,
        subject: str | None = None,
        email: str | None = None,
        groups: list[str] | None = None,
        claims: dict[str, Any] | None = None,
    ) -> SessionRecord:
        now = time.time()
        raw = secrets.token_urlsafe(32)
        session_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
        ttl = ttl_sec if ttl_sec is not None else self.ttl_sec
        rec = SessionRecord(
            session_id=session_id,
            principal_name=principal.name,
            role=principal.role,
            created_at=now,
            expires_at=now + ttl,
            auth_method=principal.auth_method,
            subject=subject,
            email=email,
            groups=list(groups or []),
            claims=dict(claims or {}),
            last_activity_at=now,
        )
        with self._lock:
            self._purge_expired()
            self._sessions[session_id] = rec
            self._persist()
        return rec

    def touch_session(self, session_id: str) -> None:
        with self._lock:
            rec = self._sessions.get(session_id)
            if rec:
                rec.last_activity_at = time.time()
                self._persist()

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            self._purge_expired()
            rec = self._sessions.get(session_id)
            if rec and rec.expires_at > time.time():
                return rec
            if session_id in self._sessions:
                del self._sessions[session_id]
            return None

    def revoke_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._persist()
                return True
            return False

    def login(
        self,
        token: str,
        *,
        client_key: str,
        rbac_enabled: bool,
        rbac_users: list[Any],
        legacy_auth_token: str,
        default_role: str = "viewer",
    ) -> tuple[SessionRecord | None, str | None]:
        if not self.check_rate_limit(client_key):
            return None, "Too many login attempts"
        principal = resolve_principal_from_token(
            token,
            rbac_enabled=rbac_enabled,
            rbac_users=rbac_users,
            legacy_auth_token=legacy_auth_token,
            default_role=default_role,
        )
        if principal is None:
            self.record_failed_login(client_key)
            return None, "Invalid token"
        return self.create_session(principal), None

    def principal_from_session(self, session_id: str) -> AuthPrincipal | None:
        rec = self.get_session(session_id)
        if not rec:
            return None
        return AuthPrincipal(name=rec.principal_name, role=rec.role, auth_method=rec.auth_method)

    def list_sessions(self) -> list[SessionRecord]:
        with self._lock:
            self._purge_expired()
            return list(self._sessions.values())
