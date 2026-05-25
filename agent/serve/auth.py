from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def extract_bearer_token(headers: dict[str, str]) -> str | None:
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def extract_query_token(path: str) -> str | None:
    parsed = urlparse(path)
    qs = parse_qs(parsed.query)
    tokens = qs.get("token")
    if tokens:
        return tokens[0]
    return None


def authorize_request(
    path: str,
    headers: dict[str, str],
    *,
    auth_token: str,
    public_paths: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Return (authorized, error_message). Empty auth_token skips auth."""
    if not auth_token:
        return True, None
    clean_path = path.split("?")[0].rstrip("/") or "/"
    if public_paths and clean_path in public_paths:
        return True, None
    token = extract_bearer_token(headers) or extract_query_token(path)
    if token != auth_token:
        return False, "Unauthorized"
    return True, None
