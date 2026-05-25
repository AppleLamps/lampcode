from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.settings import RbacUser
from agent.serve.rbac import hash_token


RBAC_USERS_FILE = Path.home() / ".agent-cli" / "rbac-users.json"


def load_dynamic_users() -> list[RbacUser]:
    if not RBAC_USERS_FILE.is_file():
        return []
    try:
        data = json.loads(RBAC_USERS_FILE.read_text(encoding="utf-8"))
        users = []
        for item in data.get("users", []):
            if isinstance(item, dict) and item.get("name"):
                users.append(
                    RbacUser(
                        name=str(item["name"]),
                        token_hash=str(item.get("token_hash", "")),
                        role=str(item.get("role", "viewer")),
                    )
                )
        return users
    except (OSError, json.JSONDecodeError):
        return []


def save_dynamic_users(users: list[RbacUser]) -> None:
    RBAC_USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "users": [
            {"name": u.name, "token_hash": u.token_hash, "role": u.role}
            for u in users
        ]
    }
    RBAC_USERS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def merge_rbac_users(config_users: list[RbacUser]) -> list[RbacUser]:
    merged: dict[str, RbacUser] = {}
    for u in config_users:
        merged[u.name] = RbacUser(name=u.name, token_hash=u.token_hash, role=u.role)
    for u in load_dynamic_users():
        merged[u.name] = u
    return list(merged.values())


def list_users(config_users: list[RbacUser]) -> list[dict[str, str]]:
    return [
        {"name": u.name, "role": u.role, "token_hash": u.token_hash[:20] + "..."}
        for u in merge_rbac_users(config_users)
    ]


def add_user(
    name: str,
    token: str,
    role: str,
    config_users: list[RbacUser],
) -> dict[str, Any]:
    users = merge_rbac_users(config_users)
    users = [u for u in users if u.name != name]
    users.append(RbacUser(name=name, token_hash=hash_token(token), role=role))
    save_dynamic_users(users)
    return {"ok": True, "name": name, "role": role}


def revoke_user(name: str, config_users: list[SettingsRbacUser]) -> dict[str, Any]:
    users = [u for u in merge_rbac_users(config_users) if u.name != name]
    save_dynamic_users(users)
    return {"ok": True, "name": name}
