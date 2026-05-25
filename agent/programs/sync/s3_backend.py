from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from agent.settings import CrossThreadS3SyncSettings


PutFn = Callable[[str, bytes], None]
GetFn = Callable[[str], bytes | None]
ListFn = Callable[[], list[str]]


class S3ProgramBackend:
    name = "s3"

    def __init__(
        self,
        settings: CrossThreadS3SyncSettings,
        *,
        put_fn: PutFn | None = None,
        get_fn: GetFn | None = None,
        list_fn: ListFn | None = None,
    ) -> None:
        self.settings = settings
        self._put = put_fn or self._http_put
        self._get = get_fn or self._http_get
        self._list = list_fn or self._http_list

    def _object_key(self, program_id: str) -> str:
        prefix = self.settings.prefix.rstrip("/")
        return f"{prefix}/{program_id}.json" if prefix else f"{program_id}.json"

    def _object_url(self, program_id: str) -> str:
        endpoint = self.settings.endpoint_url.rstrip("/")
        bucket = self.settings.bucket
        key = self._object_key(program_id)
        return f"{endpoint}/{bucket}/{key}"

    def _credentials(self) -> tuple[str, str]:
        access = os.environ.get(self.settings.access_key_env, "")
        secret = os.environ.get(self.settings.secret_key_env, "")
        return access, secret

    def _http_put(self, program_id: str, payload: bytes) -> None:
        import httpx

        url = self._object_url(program_id)
        access, secret = self._credentials()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if access and secret:
            headers["Authorization"] = f"AWS {access}:{secret}"
        resp = httpx.put(url, content=payload, headers=headers, timeout=30)
        resp.raise_for_status()

    def _http_get(self, program_id: str) -> bytes | None:
        import httpx

        url = self._object_url(program_id)
        access, secret = self._credentials()
        headers: dict[str, str] = {}
        if access and secret:
            headers["Authorization"] = f"AWS {access}:{secret}"
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content

    def _http_list(self) -> list[str]:
        return []

    def push(self, program_id: str, payload: bytes) -> None:
        self._put(program_id, payload)

    def pull(self, program_id: str) -> bytes | None:
        return self._get(program_id)

    def list_program_ids(self) -> list[str]:
        return self._list()
