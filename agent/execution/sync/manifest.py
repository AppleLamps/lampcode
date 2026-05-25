from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.execution.sync.planner import _should_exclude


@dataclass
class ManifestEntry:
    mtime: float
    size: int
    hash: str | None = None


@dataclass
class SyncManifest:
    version: int = 1
    generated_at: str = ""
    files: dict[str, ManifestEntry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "files": {
                p: {"mtime": e.mtime, "size": e.size, "hash": e.hash}
                for p, e in self.files.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> SyncManifest:
        files: dict[str, ManifestEntry] = {}
        for path, raw in data.get("files", {}).items():
            files[path] = ManifestEntry(
                mtime=float(raw.get("mtime", 0)),
                size=int(raw.get("size", 0)),
                hash=raw.get("hash"),
            )
        return cls(
            version=int(data.get("version", 1)),
            generated_at=str(data.get("generated_at", "")),
            files=files,
        )


def manifest_file_path(cwd: Path, manifest_rel: str) -> Path:
    return cwd / manifest_rel


def load_manifest(path: Path) -> SyncManifest | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SyncManifest.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def save_manifest(path: Path, manifest: SyncManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_local_manifest(
    cwd: Path,
    *,
    excludes: list[str],
    include_dotfiles: bool,
    compute_hash: bool = False,
    hash_paths: set[str] | None = None,
) -> SyncManifest:
    cwd = cwd.resolve()
    files: dict[str, ManifestEntry] = {}
    if not cwd.is_dir():
        return SyncManifest(generated_at=datetime.now(timezone.utc).isoformat(), files=files)
    for path in cwd.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = str(path.relative_to(cwd)).replace("\\", "/")
        except ValueError:
            continue
        if _should_exclude(rel, excludes, include_dotfiles):
            continue
        if rel.endswith("sync-manifest.json"):
            continue
        st = path.stat()
        entry_hash = None
        if compute_hash and (hash_paths is None or rel in hash_paths):
            try:
                entry_hash = file_hash(path)
            except OSError:
                pass
        files[rel] = ManifestEntry(mtime=st.st_mtime, size=st.st_size, hash=entry_hash)
    return SyncManifest(
        generated_at=datetime.now(timezone.utc).isoformat(),
        files=files,
    )
