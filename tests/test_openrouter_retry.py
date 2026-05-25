from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.config import Config
from agent.settings import OpenRouterSettings
from model.openrouter import OpenRouterClient, OpenRouterError


def _config(**kwargs) -> Config:
    defaults = dict(cwd=".", model="test", openrouter_api_key="test-key")
    defaults.update(kwargs)
    return Config(**defaults)


def _stream_response(chunks: list[str], *, status: int = 200, body: bytes = b""):
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.read.return_value = body
    mock_resp.headers = {}
    mock_resp.iter_lines.return_value = iter(chunks)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_retries_on_503_then_succeeds() -> None:
    config = _config(openrouter=OpenRouterSettings(max_retries=3, retry_base_delay_sec=0.01))
    client = OpenRouterClient(config)

    ok_chunks = [
        'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
        "data: [DONE]\n",
    ]

    with patch.object(client, "_sleep_backoff"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.stream.side_effect = [
                _stream_response([], status=503, body=b"unavailable"),
                _stream_response(ok_chunks),
            ]
            mock_client_cls.return_value = mock_client

            result = client.complete([{"role": "user", "content": "hi"}])
            assert result == "hello"
            assert mock_client.stream.call_count == 2


def test_raises_after_max_retries() -> None:
    config = _config(
        openrouter=OpenRouterSettings(
            max_retries=2,
            retry_base_delay_sec=0.01,
            fallback_models=[],
            fallback_on=[],
        )
    )
    client = OpenRouterClient(config)

    with patch.object(client, "_sleep_backoff") as sleep_mock:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.stream.return_value = _stream_response([], status=429, body=b"rate limited")
            mock_client_cls.return_value = mock_client

            with pytest.raises(OpenRouterError, match="429"):
                client.complete([{"role": "user", "content": "hi"}])
            assert sleep_mock.call_count == 2


def test_missing_api_key_message() -> None:
    config = Config(cwd=".", model="test", openrouter_api_key=None)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        config.require_api_key()


def test_connection_error_retries() -> None:
    config = _config(openrouter=OpenRouterSettings(max_retries=2, retry_base_delay_sec=0.01))
    client = OpenRouterClient(config)

    ok_chunks = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
        "data: [DONE]\n",
    ]

    with patch.object(client, "_sleep_backoff"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.stream.side_effect = [
                httpx.ConnectError("boom"),
                _stream_response(ok_chunks),
            ]
            mock_client_cls.return_value = mock_client

            assert client.complete([{"role": "user", "content": "x"}]) == "ok"
