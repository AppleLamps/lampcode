from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.settings import MarketplaceSettings
from agent.skills.marketplace import create_signed_bundle, generate_ed25519_keypair, marketplace_dir
from agent.skills.marketplace_remote import (
    install_from_registry,
    is_revoked,
    load_revocations,
    sync_remote_registry,
    sync_revocations,
    verify_lockfile,
    verify_registry_signature,
    write_lockfile,
)

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives.serialization import load_pem_private_key


def _sign_payload(payload: dict, private_key_pem: bytes, signed_by: str = "publisher1") -> dict:
    body = {k: v for k, v in payload.items() if k not in ("signature", "signed_by")}
    manifest_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key = load_pem_private_key(private_key_pem, password=None)
    signed = dict(payload)
    signed["signature"] = key.sign(manifest_bytes).hex()
    signed["signed_by"] = signed_by
    return signed


@pytest.fixture
def mp_settings(tmp_path: Path) -> MarketplaceSettings:
    pub, _ = generate_ed25519_keypair("publisher1", tmp_path / "marketplace")
    return MarketplaceSettings(
        enabled=True,
        require_signature=True,
        trusted_publishers=["publisher1"],
        registry_dir=str(tmp_path / "marketplace"),
        remote_registry_url="https://skills.example.com/registry.json",
        remote_registry_signature_key="publisher1",
        sync_interval_sec=3600,
        offline_cache_dir=str(tmp_path / "cache"),
        revocation_list_url="https://skills.example.com/revocations.json",
        revocation_list_ttl_sec=900,
    )


@pytest.fixture
def keypair(tmp_path: Path):
    _, priv = generate_ed25519_keypair("publisher1", tmp_path / "marketplace")
    return priv.read_bytes()


def test_verify_registry_signature_valid(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    registry = _sign_payload(
        {"version": 1, "skills": [{"name": "docs-helper", "version": "1.0.0"}]},
        keypair,
    )
    ok, reason = verify_registry_signature(registry, settings=mp_settings)
    assert ok is True
    assert reason == "ok"


def test_verify_registry_signature_invalid(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    registry = _sign_payload({"version": 1, "skills": []}, keypair)
    registry["skills"] = [{"name": "tampered"}]
    ok, reason = verify_registry_signature(registry, settings=mp_settings)
    assert ok is False


def test_verify_registry_missing_signature_required(mp_settings: MarketplaceSettings) -> None:
    ok, reason = verify_registry_signature({"version": 1, "skills": []}, settings=mp_settings)
    assert ok is False
    assert "missing" in reason


def test_sync_remote_registry_downloads_and_caches(
    mp_settings: MarketplaceSettings, keypair: bytes
) -> None:
    registry = _sign_payload(
        {
            "version": 1,
            "skills": [
                {
                    "name": "docs-helper",
                    "version": "1.0.0",
                    "publisher": "publisher1",
                    "url": "https://cdn.example/docs.askill",
                    "sha256": "abc",
                }
            ],
        },
        keypair,
    )

    def fetch(_url: str) -> dict:
        return registry

    result = sync_remote_registry(mp_settings, fetch_json=fetch, force=True)
    assert result["ok"] is True
    assert result["skills"] == 1
    cache = marketplace_dir(mp_settings.registry_dir) / "remote-registry.json"
    assert cache.is_file()


def test_sync_remote_registry_offline_cache_hit(
    mp_settings: MarketplaceSettings, keypair: bytes
) -> None:
    registry = _sign_payload({"version": 1, "skills": [{"name": "a", "version": "1.0.0"}]}, keypair)

    calls = {"n": 0}

    def fetch(_url: str) -> dict:
        calls["n"] += 1
        return registry

    sync_remote_registry(mp_settings, fetch_json=fetch, force=True)
    result = sync_remote_registry(mp_settings, fetch_json=fetch, force=False)
    assert result["ok"] is True
    assert result.get("cached") is True
    assert calls["n"] == 1


def test_sync_remote_registry_fetch_error(mp_settings: MarketplaceSettings) -> None:
    def fetch(_url: str) -> dict:
        raise RuntimeError("network down")

    result = sync_remote_registry(mp_settings, fetch_json=fetch, force=True)
    assert result["ok"] is False


def test_sync_revocations_caches(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    payload = _sign_payload({"revocations": [{"name": "bad", "version": "1.0.0", "reason": "CVE"}]}, keypair)

    result = sync_revocations(mp_settings, fetch_json=lambda _u: payload, force=True)
    assert result["ok"] is True
    assert len(load_revocations(mp_settings)) == 1


def test_is_revoked_detects_entry(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    payload = _sign_payload(
        {"revocations": [{"name": "docs-helper", "version": "1.0.0", "reason": "malware"}]},
        keypair,
    )
    sync_revocations(mp_settings, fetch_json=lambda _u: payload, force=True)
    revoked, reason = is_revoked(mp_settings, "docs-helper", "1.0.0")
    assert revoked is True
    assert "malware" in reason


def test_install_from_registry_blocked_when_revoked(
    mp_settings: MarketplaceSettings, keypair: bytes
) -> None:
    registry = _sign_payload(
        {
            "version": 1,
            "skills": [{"name": "docs-helper", "version": "1.0.0", "publisher": "publisher1"}],
        },
        keypair,
    )
    sync_remote_registry(mp_settings, fetch_json=lambda _u: registry, force=True)
    rev = _sign_payload(
        {"revocations": [{"name": "docs-helper", "version": "1.0.0", "reason": "revoked"}]},
        keypair,
    )
    sync_revocations(mp_settings, fetch_json=lambda _u: rev, force=True)
    result = install_from_registry("docs-helper", "1.0.0", settings=mp_settings)
    assert result["ok"] is False
    assert result.get("revoked") is True


def test_install_from_registry_not_in_registry(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    registry = _sign_payload({"version": 1, "skills": []}, keypair)
    sync_remote_registry(mp_settings, fetch_json=lambda _u: registry, force=True)
    result = install_from_registry("missing", "1.0.0", settings=mp_settings)
    assert result["ok"] is False


def test_lockfile_write_and_verify_ok(tmp_path: Path) -> None:
    bundle = tmp_path / "demo.askill"
    bundle.write_bytes(b"payload")
    import hashlib

    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    write_lockfile(
        [{"name": "demo", "version": "1.0.0", "sha256": digest, "bundle_path": str(bundle)}],
        tmp_path,
    )
    result = verify_lockfile(tmp_path)
    assert result["ok"] is True


def test_lockfile_verify_fails_on_tampered_sha256(tmp_path: Path) -> None:
    bundle = tmp_path / "demo.askill"
    bundle.write_bytes(b"payload")
    write_lockfile(
        [{"name": "demo", "version": "1.0.0", "sha256": "deadbeef", "bundle_path": str(bundle)}],
        tmp_path,
    )
    result = verify_lockfile(tmp_path)
    assert result["ok"] is False
    assert "mismatch" in result["error"]


def test_lockfile_missing(tmp_path: Path) -> None:
    result = verify_lockfile(tmp_path)
    assert result["ok"] is False


def test_install_from_registry_sha256_mismatch(
    mp_settings: MarketplaceSettings, keypair: bytes, tmp_path: Path
) -> None:
    registry = _sign_payload(
        {
            "version": 1,
            "skills": [
                {
                    "name": "docs-helper",
                    "version": "1.0.0",
                    "publisher": "publisher1",
                    "url": "https://cdn/bundle.askill",
                    "sha256": "expected-not-match",
                }
            ],
        },
        keypair,
    )
    sync_remote_registry(mp_settings, fetch_json=lambda _u: registry, force=True)
    cache_dir = Path(mp_settings.offline_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "docs-helper-1.0.0.askill").write_bytes(b"other-bytes")
    result = install_from_registry("docs-helper", "1.0.0", settings=mp_settings)
    assert result["ok"] is False
    assert "sha256" in result["error"]


def test_install_from_registry_downloads_bundle(
    mp_settings: MarketplaceSettings, keypair: bytes, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle.askill"
    create_signed_bundle(
        name="docs-helper",
        version="1.0.0",
        publisher="publisher1",
        skill_body="# Docs",
        private_key_pem=keypair,
        out_path=bundle,
    )
    import hashlib

    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    registry = _sign_payload(
        {
            "version": 1,
            "skills": [
                {
                    "name": "docs-helper",
                    "version": "1.0.0",
                    "publisher": "publisher1",
                    "url": "https://cdn/bundle.askill",
                    "sha256": digest,
                }
            ],
        },
        keypair,
    )
    sync_remote_registry(mp_settings, fetch_json=lambda _u: registry, force=True)
    result = install_from_registry(
        "docs-helper",
        "1.0.0",
        settings=mp_settings,
        cwd=tmp_path,
        fetch_bytes=lambda _u: bundle.read_bytes(),
    )
    assert result["ok"] is True


def test_sync_no_remote_url(mp_settings: MarketplaceSettings) -> None:
    mp_settings.remote_registry_url = ""
    result = sync_remote_registry(mp_settings, force=True)
    assert result["ok"] is False


def test_sync_revocations_no_url(mp_settings: MarketplaceSettings) -> None:
    mp_settings.revocation_list_url = ""
    result = sync_revocations(mp_settings, force=True)
    assert result["ok"] is False


def test_sync_metrics_success(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    from agent.metrics import MetricsCollector

    MetricsCollector.reset_for_tests()
    registry = _sign_payload({"version": 1, "skills": []}, keypair)
    sync_remote_registry(mp_settings, fetch_json=lambda _u: registry, force=True)
    assert MetricsCollector.global_collector().snapshot().labeled_counters["agent_marketplace_sync_total"]["success"] == 1


def test_sync_metrics_error(mp_settings: MarketplaceSettings) -> None:
    from agent.metrics import MetricsCollector

    MetricsCollector.reset_for_tests()

    def boom(_u: str) -> dict:
        raise RuntimeError("fail")

    sync_remote_registry(mp_settings, fetch_json=boom, force=True)
    assert MetricsCollector.global_collector().snapshot().labeled_counters["agent_marketplace_sync_total"]["error"] == 1


def test_revocation_cache_hit(mp_settings: MarketplaceSettings, keypair: bytes) -> None:
    payload = _sign_payload({"revocations": []}, keypair)
    calls = {"n": 0}

    def fetch(_u: str) -> dict:
        calls["n"] += 1
        return payload

    sync_revocations(mp_settings, fetch_json=fetch, force=True)
    sync_revocations(mp_settings, fetch_json=fetch, force=False)
    assert calls["n"] == 1
