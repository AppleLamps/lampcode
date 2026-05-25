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


def test_retries_on_503_then_succeeds() -> None:
    config = _config(openrouter=OpenRouterSettings(max_retries=3, retry_base_delay_sec=0.01))
    client = OpenRouterClient(config)

    fail_response = MagicMock()
    fail_response.status_code = 503
    fail_response.text = "unavailable"
    fail_response.headers = {}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.json.return_value = {
        "choices": [{"message": {"content": "hello"}}],
    }

    with patch.object(client, "_sleep_backoff"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.post.side_effect = [fail_response, ok_response]
            mock_client_cls.return_value = mock_client

            result = client.complete([{"role": "user", "content": "hi"}])
            assert result == "hello"
            assert mock_client.post.call_count == 2


def test_raises_after_max_retries() -> None:
    config = _config(openrouter=OpenRouterSettings(max_retries=2, retry_base_delay_sec=0.01))
    client = OpenRouterClient(config)

    fail_response = MagicMock()
    fail_response.status_code = 429
    fail_response.text = "rate limited"
    fail_response.headers = {"Retry-After": "1"}

    with patch.object(client, "_sleep_backoff") as sleep_mock:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.post.return_value = fail_response
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

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
    }

    with patch.object(client, "_sleep_backoff"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.post.side_effect = [httpx.ConnectError("boom"), ok_response]
            mock_client_cls.return_value = mock_client

            assert client.complete([{"role": "user", "content": "x"}]) == "ok"
