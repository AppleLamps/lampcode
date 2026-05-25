from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.settings import MarketplaceSettings
from agent.skills.marketplace import (
    create_signed_bundle,
    generate_ed25519_keypair,
    install_bundle,
    marketplace_dir,
    verify_bundle,
)


pytest.importorskip("cryptography")


@pytest.fixture
def mp_settings(tmp_path: Path) -> MarketplaceSettings:
    return MarketplaceSettings(
        enabled=True,
        allow_unsigned_local=True,
        require_signature=True,
        trusted_publishers=["publisher1"],
        registry_dir=str(tmp_path / "marketplace"),
    )


@pytest.fixture
def keypair(tmp_path: Path):
    pub, priv = generate_ed25519_keypair("publisher1", tmp_path / "marketplace")
    return pub, priv.read_bytes()


def test_valid_signed_bundle(tmp_path: Path, mp_settings: MarketplaceSettings, keypair) -> None:
    _, priv_pem = keypair
    bundle = tmp_path / "demo.askill"
    create_signed_bundle(
        name="docs-helper",
        version="1.0.0",
        publisher="publisher1",
        skill_body="---\nname: docs-helper\ndescription: help\n---\n# Docs",
        private_key_pem=priv_pem,
        out_path=bundle,
    )
    result = verify_bundle(bundle, settings=mp_settings)
    assert result.ok is True
    assert result.manifest is not None
    assert result.manifest.name == "docs-helper"


def test_tampered_signature_rejected(tmp_path: Path, mp_settings: MarketplaceSettings, keypair) -> None:
    _, priv_pem = keypair
    bundle = tmp_path / "demo.askill"
    create_signed_bundle(
        name="docs-helper",
        version="1.0.0",
        publisher="publisher1",
        skill_body="# skill",
        private_key_pem=priv_pem,
        out_path=bundle,
    )
    import tarfile

    extract = tmp_path / "extract"
    extract.mkdir()
    with tarfile.open(bundle, "r:gz") as tar:
        tar.extractall(extract)
    (extract / "SKILL.md").write_text("# tampered", encoding="utf-8")
    bad = tmp_path / "bad.askill"
    with tarfile.open(bad, "w:gz") as tar:
        for item in extract.iterdir():
            tar.add(item, arcname=item.name)
    result = verify_bundle(bad, settings=mp_settings)
    assert result.ok is False


def test_untrusted_publisher_rejected(tmp_path: Path, mp_settings: MarketplaceSettings, keypair) -> None:
    _, priv_pem = keypair
    bundle = tmp_path / "demo.askill"
    create_signed_bundle(
        name="evil",
        version="1.0.0",
        publisher="unknown-pub",
        skill_body="# skill",
        private_key_pem=priv_pem,
        out_path=bundle,
    )
    result = verify_bundle(bundle, settings=mp_settings)
    assert result.ok is False
    assert "not trusted" in result.reason


def test_unsigned_local_allowed(tmp_path: Path, mp_settings: MarketplaceSettings) -> None:
    import tarfile

    bundle = tmp_path / "local.askill"
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "SKILL.md").write_text("# local skill", encoding="utf-8")
    manifest = {
        "name": "local-skill",
        "version": "0.1.0",
        "publisher": "dev",
        "files": [{"path": "SKILL.md", "sha256": __import__("hashlib").sha256(b"# local skill").hexdigest()}],
    }
    (root / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with tarfile.open(bundle, "w:gz") as tar:
        for item in root.iterdir():
            tar.add(item, arcname=item.name)
    result = verify_bundle(bundle, settings=mp_settings)
    assert result.ok is True


def test_install_signed_bundle_user_scope(
    tmp_path: Path, mp_settings: MarketplaceSettings, keypair, monkeypatch
) -> None:
    _, priv_pem = keypair
    bundle = tmp_path / "demo.askill"
    create_signed_bundle(
        name="docs-helper",
        version="1.0.0",
        publisher="publisher1",
        skill_body="# Docs helper skill",
        private_key_pem=priv_pem,
        out_path=bundle,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = install_bundle(bundle, settings=mp_settings, scope="user")
    assert result["ok"] is True
    installed = tmp_path / ".agent-cli" / "skills" / "docs-helper" / "SKILL.md"
    assert installed.is_file()


def test_install_project_scope(tmp_path: Path, mp_settings: MarketplaceSettings, keypair) -> None:
    _, priv_pem = keypair
    bundle = tmp_path / "demo.askill"
    create_signed_bundle(
        name="proj-skill",
        version="1.0.0",
        publisher="publisher1",
        skill_body="# project",
        private_key_pem=priv_pem,
        out_path=bundle,
    )
    result = install_bundle(bundle, settings=mp_settings, scope="project", cwd=tmp_path)
    assert result["ok"] is True
    assert (tmp_path / ".agent-cli" / "skills" / "proj-skill" / "SKILL.md").is_file()


def test_require_signature_blocks_unsigned_cache(tmp_path: Path) -> None:
    import tarfile

    settings = MarketplaceSettings(
        enabled=True,
        allow_unsigned_local=False,
        allow_unsigned_cache=False,
        require_signature=True,
        registry_dir=str(tmp_path / "mp"),
    )
    cache = marketplace_dir(settings.registry_dir) / "cache"
    bundle = cache / "cached-1.0.0.tar.gz"
    root = tmp_path / "b"
    root.mkdir()
    (root / "SKILL.md").write_text("# x", encoding="utf-8")
    manifest = {
        "name": "cached",
        "version": "1.0.0",
        "publisher": "p",
        "files": [{"path": "SKILL.md", "sha256": __import__("hashlib").sha256(b"# x").hexdigest()}],
    }
    (root / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with tarfile.open(bundle, "w:gz") as tar:
        for item in root.iterdir():
            tar.add(item, arcname=item.name)
    result = verify_bundle(bundle, settings=settings)
    assert result.ok is False


def test_marketplace_dir_creates_layout(tmp_path: Path) -> None:
    root = marketplace_dir(tmp_path / "mp")
    assert (root / "keys").is_dir()
    assert (root / "cache").is_dir()


def test_hash_mismatch_rejected(tmp_path: Path, mp_settings: MarketplaceSettings, keypair) -> None:
    _, priv_pem = keypair
    bundle = tmp_path / "demo.askill"
    create_signed_bundle(
        name="docs-helper",
        version="1.0.0",
        publisher="publisher1",
        skill_body="# skill",
        private_key_pem=priv_pem,
        out_path=bundle,
    )
    import tarfile

    extract = tmp_path / "ex"
    extract.mkdir()
    with tarfile.open(bundle, "r:gz") as tar:
        tar.extractall(extract)
    manifest = json.loads((extract / "MANIFEST.json").read_text())
    manifest["files"][0]["sha256"] = "0" * 64
    (extract / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    bad = tmp_path / "bad.askill"
    with tarfile.open(bad, "w:gz") as tar:
        for item in extract.iterdir():
            tar.add(item, arcname=item.name)
    result = verify_bundle(bad, settings=mp_settings)
    assert result.ok is False
    assert "hash mismatch" in result.reason
