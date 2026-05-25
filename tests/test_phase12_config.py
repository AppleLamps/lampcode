from __future__ import annotations

from agent.config_validate import validate_config
from agent.settings import ServeRbacSettings, ServeSettings, ServeTlsSettings
from agent.serve.rbac import hash_token, resolve_principal_from_token
from agent.settings import RbacUser


def test_validate_remote_bind_without_tls_rbac() -> None:
    from agent.config import Config

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    serve = ServeSettings(host="0.0.0.0", allow_remote_bind=True, rbac=ServeRbacSettings(enabled=False))
    from agent import config_validate

    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("0.0.0.0" in i.message for i in result.errors)
    finally:
        config_validate.load_serve_settings = original


def test_admin_legacy_token_when_rbac_off() -> None:
    p = resolve_principal_from_token("secret", rbac_enabled=False, rbac_users=[], legacy_auth_token="secret")
    assert p is not None
    assert p.role == "admin"


def test_hash_token_deterministic() -> None:
    assert hash_token("a") == hash_token("a")
    assert hash_token("a") != hash_token("b")


def test_rbac_admin_has_manage_users() -> None:
    from agent.serve.rbac import Permission

    admin = resolve_principal_from_token(
        "t",
        rbac_enabled=True,
        rbac_users=[RbacUser(name="a", token_hash=hash_token("t"), role="admin")],
        legacy_auth_token="",
    )
    assert admin is not None
    assert admin.has_permission(Permission.MANAGE_USERS)


def test_self_signed_tls_config_warning() -> None:
    from agent.config import Config
    from agent import config_validate

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    serve = ServeSettings(tls=ServeTlsSettings(auto_generate_self_signed=True))
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("auto_generate_self_signed" in i.message for i in result.warnings)
    finally:
        config_validate.load_serve_settings = original


def test_rbac_enabled_no_users_error() -> None:
    from agent.config import Config
    from agent import config_validate
    from agent.serve.users import RBAC_USERS_FILE
    import json

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    serve = ServeSettings(rbac=ServeRbacSettings(enabled=True, users=[]), auth_token="")
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    if RBAC_USERS_FILE.is_file():
        RBAC_USERS_FILE.unlink()
    try:
        result = validate_config(cfg)
        assert any("no users configured" in i.message for i in result.errors)
    finally:
        config_validate.load_serve_settings = original
