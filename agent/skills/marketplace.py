from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BundleManifest:
    name: str
    version: str
    publisher: str
    files: list[dict[str, str]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifest:
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            publisher=str(data.get("publisher", "unknown")),
            files=[{"path": str(f["path"]), "sha256": str(f["sha256"])} for f in data.get("files", [])],
        )


@dataclass
class VerifyResult:
    ok: bool
    reason: str = ""
    manifest: BundleManifest | None = None


def marketplace_dir(base: str | Path | None = None) -> Path:
    root = Path(str(base or "~/.agent-cli/marketplace")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    (root / "keys").mkdir(exist_ok=True)
    (root / "cache").mkdir(exist_ok=True)
    return root


def registry_path(base: str | Path | None = None) -> Path:
    return marketplace_dir(base) / "registry.json"


def load_registry(base: str | Path | None = None) -> list[dict[str, Any]]:
    path = registry_path(base)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("skills", []))
    except (OSError, json.JSONDecodeError):
        return []


def save_registry(entries: list[dict[str, Any]], base: str | Path | None = None) -> None:
    path = registry_path(base)
    path.write_text(json.dumps({"skills": entries}, indent=2), encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_public_key(pubkey_id: str, base: Path) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise RuntimeError("cryptography required (pip install -e '.[marketplace]')") from exc
    key_path = base / "keys" / f"{pubkey_id}.ed25519.pub"
    if not key_path.is_file():
        raise FileNotFoundError(f"Public key not found: {key_path}")
    return serialization.load_pem_public_key(key_path.read_bytes())


def verify_bundle(
    bundle_path: Path,
    *,
    settings,
    allow_unsigned_local: bool | None = None,
) -> VerifyResult:
    if not bundle_path.is_file():
        return VerifyResult(ok=False, reason="bundle not found")
    base = marketplace_dir(settings.registry_dir)
    unsigned_ok = (
        allow_unsigned_local
        if allow_unsigned_local is not None
        else settings.allow_unsigned_local
    )
    is_local = not str(bundle_path).startswith(str(base / "cache"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(tmp_path)
        manifest_path = tmp_path / "MANIFEST.json"
        skill_path = tmp_path / "SKILL.md"
        sig_path = tmp_path / "SIGNATURE.ed25519"
        if not manifest_path.is_file() or not skill_path.is_file():
            return VerifyResult(ok=False, reason="missing SKILL.md or MANIFEST.json")
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = BundleManifest.from_dict(json.loads(manifest_bytes.decode("utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            return VerifyResult(ok=False, reason=f"invalid manifest: {exc}")

        for entry in manifest.files:
            rel = entry["path"]
            fpath = tmp_path / rel
            if not fpath.is_file():
                return VerifyResult(ok=False, reason=f"missing file {rel}")
            if _sha256_file(fpath) != entry["sha256"]:
                return VerifyResult(ok=False, reason=f"hash mismatch {rel}")

        signed = sig_path.is_file()
        if not signed:
            if settings.require_signature and not (unsigned_ok and is_local):
                return VerifyResult(ok=False, reason="signature required")
            return VerifyResult(ok=True, manifest=manifest, reason="unsigned accepted")

        if settings.trusted_publishers and manifest.publisher not in settings.trusted_publishers:
            return VerifyResult(ok=False, reason=f"publisher '{manifest.publisher}' not trusted")

        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            pubkey = _load_public_key(manifest.publisher, base)
            if not isinstance(pubkey, Ed25519PublicKey):
                return VerifyResult(ok=False, reason="invalid public key type")
            signature = sig_path.read_bytes()
            pubkey.verify(signature, manifest_bytes)
        except InvalidSignature:
            return VerifyResult(ok=False, reason="invalid signature")
        except Exception as exc:
            return VerifyResult(ok=False, reason=str(exc))

        return VerifyResult(ok=True, manifest=manifest)


def install_bundle(
    bundle_path: Path,
    *,
    settings,
    scope: str = "user",
    cwd: Path | None = None,
    emitter=None,
) -> dict[str, Any]:
    result = verify_bundle(bundle_path, settings=settings)
    if not result.ok or result.manifest is None:
        return {"ok": False, "error": result.reason}
    manifest = result.manifest
    if scope == "project":
        dest_root = (cwd or Path.cwd()) / ".agent-cli" / "skills" / manifest.name
    else:
        dest_root = Path.home() / ".agent-cli" / "skills" / manifest.name

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(tmp_path)
        if dest_root.exists():
            shutil.rmtree(dest_root)
        dest_root.mkdir(parents=True, exist_ok=True)
        for entry in manifest.files:
            src = tmp_path / entry["path"]
            dst = dest_root / entry["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    cache_dir = marketplace_dir(settings.registry_dir) / "cache"
    cache_name = f"{manifest.name}-{manifest.version}.tar.gz"
    if not bundle_path.resolve().parent.samefile(cache_dir):
        shutil.copy2(bundle_path, cache_dir / cache_name)

    if emitter:
        emitter.skills_marketplace_installed(
            manifest.name,
            version=manifest.version,
            publisher=manifest.publisher,
            scope=scope,
        )

    return {
        "ok": True,
        "name": manifest.name,
        "version": manifest.version,
        "path": str(dest_root),
        "scope": scope,
    }


def list_marketplace(settings) -> list[dict[str, Any]]:
    return load_registry(settings.registry_dir)


def uninstall_skill(name: str, *, scope: str = "user", cwd: Path | None = None) -> dict[str, Any]:
    if scope == "project":
        dest = (cwd or Path.cwd()) / ".agent-cli" / "skills" / name
    else:
        dest = Path.home() / ".agent-cli" / "skills" / name
    if not dest.is_dir():
        return {"ok": False, "error": "skill not installed"}
    shutil.rmtree(dest)
    return {"ok": True, "name": name, "scope": scope}


def create_signed_bundle(
    *,
    name: str,
    version: str,
    publisher: str,
    skill_body: str,
    private_key_pem: bytes,
    extra_files: dict[str, str] | None = None,
    out_path: Path,
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_md = root / "SKILL.md"
        skill_md.write_text(skill_body, encoding="utf-8")
        files = [{"path": "SKILL.md", "sha256": _sha256_file(skill_md)}]
        for rel, content in (extra_files or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            files.append({"path": rel, "sha256": _sha256_file(p)})
        manifest = {
            "name": name,
            "version": version,
            "publisher": publisher,
            "files": files,
        }
        manifest_path = root / "MANIFEST.json"
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        manifest_path.write_bytes(manifest_bytes)
        key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("Ed25519 private key required")
        sig = key.sign(manifest_bytes)
        (root / "SIGNATURE.ed25519").write_bytes(sig)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out_path, "w:gz") as tar:
            for item in root.iterdir():
                tar.add(item, arcname=item.name)


def generate_ed25519_keypair(publisher: str, base: str | Path | None = None) -> tuple[Path, Path]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    root = marketplace_dir(base)
    private_key = Ed25519PrivateKey.generate()
    pub_path = root / "keys" / f"{publisher}.ed25519.pub"
    priv_path = root / "keys" / f"{publisher}.ed25519"
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
