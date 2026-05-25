from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any


def canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def default_keys_dir() -> Path:
    return Path.home() / ".agent-cli" / "programs" / "keys"


def key_paths(key_id: str, base: Path | None = None) -> tuple[Path, Path]:
    root = base or default_keys_dir()
    return root / f"{key_id}.ed25519.pub", root / f"{key_id}.ed25519"


def sign_state(state: dict[str, Any], *, key_id: str, keys_dir: Path | None = None) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _, priv_path = key_paths(key_id, keys_dir)
    if not priv_path.is_file():
        raise FileNotFoundError(f"Signing key not found: {priv_path}")
    key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("Ed25519 private key required")
    blob = canonical_json(state)
    sig = key.sign(blob)
    return {
        "state": state,
        "signature": base64.b64encode(sig).decode("ascii"),
        "signing_key_id": key_id,
    }


def verify_signed_payload(payload: dict[str, Any], *, keys_dir: Path | None = None) -> bool:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    state = payload.get("state")
    sig_b64 = payload.get("signature")
    key_id = payload.get("signing_key_id", "program-sync")
    if not isinstance(state, dict) or not sig_b64:
        return False
    pub_path, _ = key_paths(str(key_id), keys_dir)
    if not pub_path.is_file():
        return False
    key = serialization.load_pem_public_key(pub_path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        return False
    try:
        sig = base64.b64decode(sig_b64)
        key.verify(sig, canonical_json(state))
        return True
    except Exception:
        return False


def generate_signing_keypair(key_id: str, base: Path | None = None) -> tuple[Path, Path]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pub_path, priv_path = key_paths(key_id, base)
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    pub_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    priv_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return pub_path, priv_path
