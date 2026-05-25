from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.auth.oidc_tokens import OidcTokenRecord, OidcTokenStore
from agent.auth.refresh import RefreshError, ensure_fresh_tokens, needs_refresh, refresh_tokens
from agent.auth.storage import AuthStorageSettings, FileAuthStorage, KeyringAuthStorage, select_backend
from agent.metrics import MetricsCollector
from agent.serve.oidc import OidcRoleMapping, ServeOidcSettings


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    MetricsCollector.reset_for_tests()
    yield
    MetricsCollector.reset_for_tests()


def _oidc_settings(**kw) -> ServeOidcSettings:
    defaults = dict(
        enabled=True,
        issuer_url="https://idp.example.com",
        client_id="cid",
        refresh_rotation=True,
        refresh_skew_sec=300,
        role_mapping=OidcRoleMapping(default_role="viewer"),
    )
    defaults.update(kw)
    return ServeOidcSettings(**defaults)


def test_needs_refresh_within_skew() -> None:
    record = OidcTokenRecord(
        issuer_url="https://idp.example.com",
        client_id="cid",
        expires_at=time.time() + 100,
        refresh_token="rt",
    )
    assert needs_refresh(record, skew_sec=300) is True


def test_needs_refresh_not_yet() -> None:
    record = OidcTokenRecord(
        issuer_url="https://idp.example.com",
        client_id="cid",
        expires_at=time.time() + 3600,
        refresh_token="rt",
    )
    assert needs_refresh(record, skew_sec=300) is False


def test_needs_refresh_no_refresh_token_expired() -> None:
    record = OidcTokenRecord(
        issuer_url="https://idp.example.com",
        client_id="cid",
        expires_at=time.time() - 10,
        refresh_token="",
    )
    assert needs_refresh(record) is True


def test_refresh_replaces_rotated_refresh_token(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    record = OidcTokenRecord(
        issuer_url="https://idp.example.com",
        client_id="cid",
        access_token="old-at",
        refresh_token="old-rt",
        expires_at=time.time() - 10,
    )
    store.save(record)
    settings = _oidc_settings()
    mock_client = MagicMock()
    mock_client.refresh_token.return_value = {
        "access_token": "new-at",
        "refresh_token": "new-rt",
        "expires_in": 3600,
    }
    mock_client.claims_from_token_response.return_value = {"sub": "u1", "email": "a@x.com"}
    mock_client.map_role.return_value = "viewer"
    with patch("agent.auth.refresh.OidcClient", return_value=mock_client):
        updated = refresh_tokens(store, settings, force=True)
    assert updated.access_token == "new-at"
    assert updated.refresh_token == "new-rt"
    loaded = store.load()
    assert loaded is not None
    assert loaded.refresh_token == "new-rt"


def test_refresh_invalid_clears_store(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            refresh_token="bad-rt",
            expires_at=time.time() - 10,
        )
    )
    mock_client = MagicMock()
    mock_client.refresh_token.side_effect = RuntimeError("invalid_grant")
    with patch("agent.auth.refresh.OidcClient", return_value=mock_client):
        with pytest.raises(RefreshError):
            refresh_tokens(store, _oidc_settings(), force=True)
    assert store.load() is None


def test_refresh_metrics_success(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            refresh_token="rt",
            expires_at=time.time() - 10,
        )
    )
    mock_client = MagicMock()
    mock_client.refresh_token.return_value = {"access_token": "at", "expires_in": 3600}
    mock_client.claims_from_token_response.return_value = {}
    mock_client.map_role.return_value = "viewer"
    with patch("agent.auth.refresh.OidcClient", return_value=mock_client):
        refresh_tokens(store, _oidc_settings(), force=True)
    assert MetricsCollector.global_collector().snapshot().labeled_counters["agent_auth_refresh_total"]["success"] == 1


def test_refresh_metrics_error(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            refresh_token="rt",
            expires_at=time.time() - 10,
        )
    )
    mock_client = MagicMock()
    mock_client.refresh_token.side_effect = RuntimeError("fail")
    with patch("agent.auth.refresh.OidcClient", return_value=mock_client):
        with pytest.raises(RefreshError):
            refresh_tokens(store, _oidc_settings(), force=True)
    assert MetricsCollector.global_collector().snapshot().labeled_counters["agent_auth_refresh_total"]["error"] == 1


def test_ensure_fresh_tokens_refreshes_when_needed(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            access_token="old",
            refresh_token="rt",
            expires_at=time.time() - 1,
        )
    )
    mock_client = MagicMock()
    mock_client.refresh_token.return_value = {"access_token": "fresh", "expires_in": 3600}
    mock_client.claims_from_token_response.return_value = {}
    mock_client.map_role.return_value = "viewer"
    with patch("agent.auth.refresh.OidcClient", return_value=mock_client):
        record = ensure_fresh_tokens(store, _oidc_settings())
    assert record is not None
    assert record.access_token == "fresh"


def test_ensure_fresh_tokens_noop_without_settings(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            access_token="at",
            expires_at=time.time() + 3600,
        )
    )
    record = ensure_fresh_tokens(store, None)
    assert record is not None
    assert record.access_token == "at"


def test_file_storage_restricted_permissions(tmp_path: Path) -> None:
    import os
    import stat

    path = tmp_path / "auth" / "oidc.json"
    storage = FileAuthStorage(path)
    storage.save({"access_token": "secret"})
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600
    else:
        assert path.is_file()


def test_keyring_backend_selected_when_available(monkeypatch) -> None:
    monkeypatch.setattr("agent.auth.storage.keyring_available", lambda: True)
    assert select_backend(AuthStorageSettings(backend="auto")) == "keyring"


def test_file_fallback_when_keyring_missing(monkeypatch) -> None:
    monkeypatch.setattr("agent.auth.storage.keyring_available", lambda: False)
    assert select_backend(AuthStorageSettings(backend="auto")) == "file"
    assert select_backend(AuthStorageSettings(backend="keyring")) == "file"


def test_keyring_storage_mocked(monkeypatch) -> None:
    saved: dict[str, str] = {}

    class FakeKeyring:
        @staticmethod
        def set_password(service, account, value):
            saved[f"{service}:{account}"] = value

        @staticmethod
        def get_password(service, account):
            return saved.get(f"{service}:{account}")

        class errors:
            class PasswordDeleteError(Exception):
                pass

        @staticmethod
        def delete_password(service, account):
            saved.pop(f"{service}:{account}", None)

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyring)
    storage = KeyringAuthStorage("agent-cli")
    storage.save({"refresh_token": "rt", "access_token": "at"})
    loaded = storage.load()
    assert loaded["refresh_token"] == "rt"
    storage.clear()
    assert storage.load() is None


def test_redacted_summary_never_includes_tokens() -> None:
    record = OidcTokenRecord(
        issuer_url="https://idp.example.com",
        client_id="cid",
        access_token="super-secret",
        refresh_token="also-secret",
        email="user@example.com",
    )
    summary = record.redacted_summary()
    dumped = json.dumps(summary)
    assert "super-secret" not in dumped
    assert "also-secret" not in dumped
    assert summary["has_refresh_token"] is True


def test_store_keyring_fallback_to_file(tmp_path: Path, monkeypatch) -> None:
    file_path = tmp_path / "oidc.json"
    FileAuthStorage(file_path).save({"issuer_url": "x", "client_id": "c", "access_token": "at"})
    monkeypatch.setattr("agent.auth.storage.keyring_available", lambda: True)

    class EmptyKeyring:
        @staticmethod
        def get_password(*_a, **_k):
            return None

        @staticmethod
        def set_password(*_a, **_k):
            pass

        @staticmethod
        def delete_password(*_a, **_k):
            pass

        class errors:
            class PasswordDeleteError(Exception):
                pass

    monkeypatch.setitem(__import__("sys").modules, "keyring", EmptyKeyring)
    store = OidcTokenStore(file_path, storage_settings=AuthStorageSettings(backend="keyring"))
    record = store.load()
    assert record is not None
    assert record.access_token == "at"


def test_refresh_no_tokens_raises(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "missing.json", storage_settings=AuthStorageSettings(backend="file"))
    with patch.object(store, "load", return_value=None), patch("agent.auth.refresh.OidcClient", MagicMock()):
        with pytest.raises(RefreshError, match="No stored tokens"):
            refresh_tokens(store, _oidc_settings(), force=True)


def test_refresh_no_refresh_token_raises(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            access_token="at",
            refresh_token="",
            expires_at=time.time() + 3600,
        )
    )
    with pytest.raises(RefreshError, match="No refresh token"):
        refresh_tokens(store, _oidc_settings(), force=True)


def test_ensure_fresh_skips_when_fresh(tmp_path: Path) -> None:
    store = OidcTokenStore(tmp_path / "oidc.json")
    store.save(
        OidcTokenRecord(
            issuer_url="https://idp.example.com",
            client_id="cid",
            access_token="at",
            refresh_token="rt",
            expires_at=time.time() + 7200,
        )
    )
    with patch("agent.auth.refresh.refresh_tokens") as mock_refresh:
        record = ensure_fresh_tokens(store, _oidc_settings())
    mock_refresh.assert_not_called()
    assert record.access_token == "at"
