from __future__ import annotations

from agent.config_validate import validate_config
from agent.config import Config
from agent.settings import ServeRbacSettings, ServeSettings, ServeTlsSettings
from agent.serve.oidc import OidcRoleMapping, ServeOidcSettings


def test_oidc_requires_tls() -> None:
    from agent import config_validate

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    serve = ServeSettings(
        auth_mode="oidc",
        tls=ServeTlsSettings(enabled=False),
        oidc=ServeOidcSettings(
            enabled=True,
            issuer_url="https://idp.example.com",
            client_id="cid",
        ),
    )
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("OIDC requires TLS" in i.message for i in result.errors)
    finally:
        config_validate.load_serve_settings = original


def test_oidc_missing_client_id() -> None:
    from agent import config_validate

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    serve = ServeSettings(
        tls=ServeTlsSettings(enabled=True),
        oidc=ServeOidcSettings(enabled=True, issuer_url="https://idp.example.com", client_id=""),
    )
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("client_id missing" in i.message for i in result.errors)
    finally:
        config_validate.load_serve_settings = original


def test_kernel_sandbox_unavailable_warning() -> None:
    from agent import config_validate
    from agent.sandbox.kernel import KernelSandboxSettings

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    cfg.sandbox_kernel = KernelSandboxSettings(enabled=True, backend="bubblewrap")
    serve = ServeSettings()
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("Kernel sandbox enabled" in i.message for i in result.warnings)
    finally:
        config_validate.load_serve_settings = original


def test_oidc_rbac_warning() -> None:
    from agent import config_validate

    cfg = Config(cwd=__import__("pathlib").Path("."), model="m", openrouter_api_key="x")
    serve = ServeSettings(
        rbac=ServeRbacSettings(enabled=False),
        tls=ServeTlsSettings(enabled=True),
        oidc=ServeOidcSettings(
            enabled=True,
            issuer_url="https://idp.example.com",
            client_id="cid",
            role_mapping=OidcRoleMapping(),
        ),
    )
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("OIDC enabled without RBAC" in i.message for i in result.warnings)
    finally:
        config_validate.load_serve_settings = original
