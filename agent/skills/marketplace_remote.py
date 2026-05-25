from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.metrics import MetricsCollector
from agent.skills.marketplace import marketplace_dir, save_registry


@dataclass
class SyncState:
    last_sync_at: float = 0.0
    last_revocation_sync_at: float = 0.0
    registry_version: int = 0


def sync_state_path(base: Path) -> Path:
    return base / "sync-state.json"


def load_sync_state(base: Path) -> SyncState:
    path = sync_state_path(base)
    if not path.is_file():
        return SyncState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SyncState(
            last_sync_at=float(data.get("last_sync_at", 0)),
            last_revocation_sync_at=float(data.get("last_revocation_sync_at", 0)),
            registry_version=int(data.get("registry_version", 0)),
        )
    except (json.JSONDecodeError, TypeError):
        return SyncState()


def save_sync_state(base: Path, state: SyncState) -> None:
    sync_state_path(base).write_text(
        json.dumps(
            {
                "last_sync_at": state.last_sync_at,
                "last_revocation_sync_at": state.last_revocation_sync_at,
                "registry_version": state.registry_version,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def verify_registry_signature(registry: dict[str, Any], *, settings) -> tuple[bool, str]:
    signature = registry.get("signature")
    signed_by = registry.get("signed_by", settings.remote_registry_signature_key)
    if not signature or not signed_by:
        if settings.require_signature:
            return False, "registry signature missing"
        return True, "unsigned accepted"
    payload = {k: v for k, v in registry.items() if k not in ("signature", "signed_by")}
    manifest_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from agent.skills.marketplace import _load_public_key

        base = marketplace_dir(settings.registry_dir)
        pubkey = _load_public_key(signed_by, base)
        if not isinstance(pubkey, Ed25519PublicKey):
            return False, "invalid public key type"
        sig_bytes = bytes.fromhex(signature) if isinstance(signature, str) else signature
        pubkey.verify(sig_bytes, manifest_bytes)
        return True, "ok"
    except InvalidSignature:
        return False, "invalid registry signature"
    except Exception as exc:
        return False, str(exc)


def sync_remote_registry(
    settings,
    *,
    fetch_json: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    base = marketplace_dir(settings.registry_dir)
    state = load_sync_state(base)
    now = time.time()
    if (
        not force
        and settings.sync_interval_sec > 0
        and state.last_sync_at
        and (now - state.last_sync_at) < settings.sync_interval_sec
    ):
        return {"ok": True, "cached": True, "skills": len(load_local_registry(settings))}

    if not settings.remote_registry_url:
        MetricsCollector.global_collector().inc_labeled("agent_marketplace_sync_total", "skipped")
        return {"ok": False, "error": "remote_registry_url not configured"}

    fetch = fetch_json or _default_fetch
    try:
        registry = fetch(settings.remote_registry_url)
    except Exception as exc:
        MetricsCollector.global_collector().inc_labeled("agent_marketplace_sync_total", "error")
        return {"ok": False, "error": f"fetch failed: {exc}"}

    ok, reason = verify_registry_signature(registry, settings=settings)
    if not ok:
        MetricsCollector.global_collector().inc_labeled("agent_marketplace_sync_total", "error")
        return {"ok": False, "error": reason}

    skills = list(registry.get("skills", []))
    cache_path = base / "remote-registry.json"
    cache_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    save_registry(skills, settings.registry_dir)
    state.last_sync_at = now
    state.registry_version = int(registry.get("version", 1))
    save_sync_state(base, state)
    MetricsCollector.global_collector().inc_labeled("agent_marketplace_sync_total", "success")
    return {"ok": True, "skills": len(skills), "cached": False}


def load_local_registry(settings) -> list[dict[str, Any]]:
    from agent.skills.marketplace import load_registry

    return load_registry(settings.registry_dir)


def revocations_path(base: Path) -> Path:
    return base / "revocations.json"


def sync_revocations(settings, *, fetch_json: Any = None, force: bool = False) -> dict[str, Any]:
    base = marketplace_dir(settings.registry_dir)
    state = load_sync_state(base)
    now = time.time()
    if (
        not force
        and settings.revocation_list_ttl_sec > 0
        and state.last_revocation_sync_at
        and (now - state.last_revocation_sync_at) < settings.revocation_list_ttl_sec
    ):
        return {"ok": True, "cached": True, "revocations": len(load_revocations(settings))}

    if not settings.revocation_list_url:
        return {"ok": False, "error": "revocation_list_url not configured"}

    fetch = fetch_json or _default_fetch
    try:
        payload = fetch(settings.revocation_list_url)
    except Exception as exc:
        return {"ok": False, "error": f"fetch failed: {exc}"}

    ok, reason = verify_registry_signature(payload, settings=settings)
    if not ok and settings.require_signature:
        return {"ok": False, "error": reason}

    revocations_path(base).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    state.last_revocation_sync_at = now
    save_sync_state(base, state)
    return {"ok": True, "revocations": len(payload.get("revocations", []))}


def load_revocations(settings) -> list[dict[str, Any]]:
    path = revocations_path(marketplace_dir(settings.registry_dir))
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("revocations", []))
    except (json.JSONDecodeError, TypeError):
        return []


def is_revoked(settings, name: str, version: str) -> tuple[bool, str]:
    for item in load_revocations(settings):
        if item.get("name") == name and item.get("version") == version:
            return True, str(item.get("reason", "revoked"))
    return False, ""


def default_lockfile_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".agent-cli" / "skills.lock.json"


def write_lockfile(entries: list[dict[str, Any]], cwd: Path | None = None) -> Path:
    path = default_lockfile_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "skills": entries}, indent=2), encoding="utf-8")
    return path


def verify_lockfile(cwd: Path | None = None) -> dict[str, Any]:
    path = default_lockfile_path(cwd)
    if not path.is_file():
        return {"ok": False, "error": "lockfile not found"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        skills = list(data.get("skills", []))
        for skill in skills:
            name = skill.get("name", "")
            version = skill.get("version", "")
            expected = skill.get("sha256", "")
            bundle = Path(skill.get("bundle_path", ""))
            if expected and bundle.is_file():
                digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
                if digest != expected:
                    return {"ok": False, "error": f"sha256 mismatch for {name}@{version}"}
        return {"ok": True, "skills": len(skills)}
    except (json.JSONDecodeError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


def install_from_registry(
    name: str,
    version: str,
    *,
    settings,
    cwd: Path | None = None,
    fetch_bytes: Any = None,
    emitter=None,
) -> dict[str, Any]:
    revoked, reason = is_revoked(settings, name, version)
    if revoked:
        return {"ok": False, "error": f"revoked: {reason}", "revoked": True}

    entries = load_local_registry(settings)
    match = next((e for e in entries if e.get("name") == name and e.get("version") == version), None)
    if not match:
        return {"ok": False, "error": f"{name}@{version} not in registry"}

    url = match.get("url", "")
    expected_sha = match.get("sha256", "")
    cache_dir = Path(str(settings.offline_cache_dir)).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = cache_dir / f"{name}-{version}.askill"

    if not bundle_path.is_file():
        if not url:
            return {"ok": False, "error": "bundle URL missing"}
        fetch = fetch_bytes or _default_fetch_bytes
        try:
            bundle_path.write_bytes(fetch(url))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    if expected_sha:
        digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        if digest != expected_sha:
            return {"ok": False, "error": "registry sha256 mismatch"}

    from agent.skills.marketplace import install_bundle

    return install_bundle(bundle_path, settings=settings, scope="project", cwd=cwd, emitter=emitter)


def _default_fetch(url: str) -> dict[str, Any]:
    import httpx

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _default_fetch_bytes(url: str) -> bytes:
    import httpx

    resp = httpx.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content
