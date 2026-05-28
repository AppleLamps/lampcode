from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def path_to_uri(path: Path) -> str:
    resolved = path.resolve()
    posix = resolved.as_posix()
    if len(posix) >= 2 and posix[1] == ":":
        return "file:///" + quote(posix.replace("\\", "/"), safe="/:")
    return "file://" + quote(posix, safe="/:")


def uri_to_path(uri: str) -> Path:
    if not uri.startswith("file://"):
        raise ValueError(f"Unsupported URI: {uri}")
    raw = uri[7:]
    if raw.startswith("/") and len(raw) > 3 and raw[2] == ":":
        raw = raw[1:]
    from urllib.parse import unquote

    return Path(unquote(raw))
