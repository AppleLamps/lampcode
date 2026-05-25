from __future__ import annotations

import json
import os
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AuthStorageSettings:
    backend: str = "auto"  # auto | keyring | file
    service_name: str = "agent-cli"
    file_path: str = ""  # default via oidc_tokens.default_oidc_auth_path


def keyring_available() -> bool:
    try:
        import keyring  # noqa: F401

        return True
    except ImportError:
        return False


def select_backend(settings: AuthStorageSettings) -> str:
    mode = (settings.backend or "auto").lower()
    if mode == "keyring":
        return "keyring" if keyring_available() else "file"
    if mode == "file":
        return "file"
    return "keyring" if keyring_available() else "file"


class AuthStorageBackend(ABC):
    @abstractmethod
    def save(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def load(self) -> dict[str, Any] | None: ...

    @abstractmethod
    def clear(self) -> None: ...

    @property
    @abstractmethod
    def backend_name(self) -> str: ...


class FileAuthStorage(AuthStorageBackend):
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def backend_name(self) -> str:
        return "file"

    def save(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 2, "tokens": record}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data.get("tokens", data)
        except (json.JSONDecodeError, TypeError):
            return None

    def clear(self) -> None:
        if self.path.is_file():
            self.path.unlink()


class KeyringAuthStorage(AuthStorageBackend):
    ACCOUNT = "oidc-tokens"

    def __init__(self, service_name: str = "agent-cli") -> None:
        self.service_name = service_name

    @property
    def backend_name(self) -> str:
        return "keyring"

    def save(self, record: dict[str, Any]) -> None:
        import keyring

        keyring.set_password(self.service_name, self.ACCOUNT, json.dumps(record))

    def load(self) -> dict[str, Any] | None:
        import keyring

        raw = keyring.get_password(self.service_name, self.ACCOUNT)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def clear(self) -> None:
        import keyring

        try:
            keyring.delete_password(self.service_name, self.ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            pass


def create_storage(settings: AuthStorageSettings, *, file_path: Path | None = None) -> AuthStorageBackend:
    backend = select_backend(settings)
    if backend == "keyring":
        return KeyringAuthStorage(settings.service_name)
    from agent.auth.oidc_tokens import default_oidc_auth_path

    path = file_path or Path(settings.file_path).expanduser() if settings.file_path else default_oidc_auth_path()
    return FileAuthStorage(path)
