from __future__ import annotations

import ssl
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config_validate import validate_config
from agent.config import Config
from agent.serve.server import AgentHttpHandler, ServeContext
from agent.serve.tls import load_ssl_context, validate_client_ca
from agent.settings import ServeSettings, ServeTlsSettings


def test_load_ssl_context_basic(tmp_path: Path) -> None:
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    _write_self_signed(cert, key)
    ctx = load_ssl_context(cert, key)
    assert isinstance(ctx, ssl.SSLContext)


def test_mtls_requires_ca_file(tmp_path: Path) -> None:
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    _write_self_signed(cert, key)
    with pytest.raises(FileNotFoundError):
        load_ssl_context(cert, key, require_client_cert=True, client_ca_file=tmp_path / "missing.ca")


def test_mtls_loads_with_ca(tmp_path: Path) -> None:
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    ca = tmp_path / "client-ca.pem"
    _write_self_signed(cert, key)
    ca.write_text(cert.read_text(), encoding="utf-8")
    ctx = load_ssl_context(cert, key, require_client_cert=True, client_ca_file=ca)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_validate_client_ca_missing() -> None:
    ok, msg = validate_client_ca(Path("/nonexistent/ca.pem"))
    assert ok is False
    assert "not found" in msg


def test_validate_client_ca_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pem"
    bad.write_text("not a cert", encoding="utf-8")
    ok, msg = validate_client_ca(bad)
    assert ok is False


def test_validate_client_ca_valid(tmp_path: Path) -> None:
    cert = tmp_path / "ca.pem"
    key = tmp_path / "k.pem"
    _write_self_signed(cert, key)
    ok, msg = validate_client_ca(cert)
    assert ok is True


def test_handler_rejects_without_client_cert() -> None:
    handler = AgentHttpHandler.__new__(AgentHttpHandler)
    handler.command = "GET"
    handler.path = "/threads"
    handler.headers = {"Authorization": "Bearer tok"}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = BytesIO()
    handler.connection = MagicMock()
    handler.connection.getpeercert.return_value = None
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.ctx = ServeContext(
        store=MagicMock(),
        run_store=MagicMock(),
        settings=ServeSettings(
            auth_token="tok",
            tls=ServeTlsSettings(enabled=True, require_client_cert=True),
        ),
        auth_token="tok",
    )
    with patch.object(handler, "_client_cert_present", return_value=False):
        ok = handler._authorize()
    assert ok is False
    assert handler.send_response.call_args[0][0] == 401


def test_config_validate_mtls_missing_ca(tmp_path: Path) -> None:
    from agent import config_validate

    cfg = Config(cwd=tmp_path, model="m", openrouter_api_key="x")
    serve = ServeSettings(
        tls=ServeTlsSettings(
            enabled=True,
            require_client_cert=True,
            client_ca_file=str(tmp_path / "missing.ca"),
        )
    )
    original = config_validate.load_serve_settings
    config_validate.load_serve_settings = lambda path=None: serve
    try:
        result = validate_config(cfg)
        assert any("mTLS client CA" in i.message for i in result.errors)
    finally:
        config_validate.load_serve_settings = original


def _write_self_signed(cert_file: Path, key_file: Path) -> None:
    from agent.serve.tls import generate_self_signed_cert

    generate_self_signed_cert(cert_file, key_file, host="127.0.0.1")
