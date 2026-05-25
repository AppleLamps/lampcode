from __future__ import annotations

from typing import Any, Callable


def introspect_token(
    token: str,
    *,
    url: str,
    client_id: str = "",
    client_secret: str = "",
    http_post: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not url:
        return {"active": True, "skipped": True}
    post = http_post or _default_post
    try:
        resp = post(
            url,
            data={"token": token, "client_id": client_id, "client_secret": client_secret},
            timeout=10,
        )
        if hasattr(resp, "json"):
            data = resp.json()
        elif isinstance(resp, dict):
            data = resp
        else:
            return {"active": False, "error": "invalid introspection response"}
        return {"active": bool(data.get("active", False)), **data}
    except Exception as exc:
        return {"active": False, "error": str(exc)}


def _default_post(url: str, *, data: dict, timeout: int) -> Any:
    import httpx

    resp = httpx.post(url, data=data, timeout=timeout)
    resp.raise_for_status()
    return resp
