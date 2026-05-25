from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.config import Config
from agent.providers.openrouter import (
    build_response_format,
    enrich_usage,
    model_supports_structured_outputs,
    use_native_model_routing,
)
from agent.settings import OpenRouterSettings, SwarmBudgetPricing
from model.openrouter import OpenRouterClient


def _config(**kwargs) -> Config:
    defaults = dict(cwd=".", model="primary/model", openrouter_api_key="test-key")
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


def test_enrich_usage_prefers_api_cost() -> None:
    cfg = _config(
        openrouter=OpenRouterSettings(
            pricing={"anthropic/claude-sonnet-4": SwarmBudgetPricing(3.0, 15.0)}
        )
    )
    out = enrich_usage(
        cfg,
        model_used="anthropic/claude-sonnet-4",
        fallback_used=False,
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.012345},
    )
    assert out["estimated_cost_usd"] == pytest.approx(0.012345)
    assert out["cost_source"] == "api"


def test_build_response_format_strict_json_schema() -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    fmt = build_response_format(schema)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == schema


def test_model_supports_structured_outputs_from_cache() -> None:
    models = [
        {
            "id": "openai/gpt-4.1",
            "supported_parameters": ["tools", "structured_outputs"],
        }
    ]
    assert model_supports_structured_outputs("openai/gpt-4.1", models) is True
    assert model_supports_structured_outputs("unknown/model", models) is False


def test_use_native_model_routing_requires_chain() -> None:
    cfg = _config(
        openrouter=OpenRouterSettings(
            native_fallback=True,
            fallback_models=["fallback/model"],
        )
    )
    from agent.providers.openrouter import model_chain

    chain = model_chain(cfg)
    assert use_native_model_routing(cfg, chain) is True

    cfg_single = _config(openrouter=OpenRouterSettings(native_fallback=True))
    assert use_native_model_routing(cfg_single, model_chain(cfg_single)) is False


def test_native_fallback_single_request_payload() -> None:
    cfg = _config(
        openrouter=OpenRouterSettings(
            native_fallback=True,
            fallback_models=["fallback/model"],
            max_retries=0,
        )
    )
    client = OpenRouterClient(cfg)
    ok_chunks = [
        'data: {"model":"fallback/model","choices":[{"delta":{"content":"ok"}}]}\n',
        "data: [DONE]\n",
    ]

    with patch.object(client, "_sleep_backoff"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.stream.return_value = _stream_response(ok_chunks)
            mock_client_cls.return_value = mock_client

            result = client.stream_completion([{"role": "user", "content": "hi"}])
            assert result.content == "ok"
            assert result.model_used == "fallback/model"
            assert result.fallback_used is True
            assert mock_client.stream.call_count == 1
            payload = mock_client.stream.call_args.kwargs["json"]
            assert payload["model"] == "primary/model"
            assert payload["models"] == ["fallback/model"]
            assert payload["route"] == "fallback"


def test_reasoning_and_details_captured_from_stream() -> None:
    cfg = _config()
    client = OpenRouterClient(cfg)
    chunks = [
        'data: {"choices":[{"delta":{"reasoning":"think","reasoning_details":[{"id":"r1","type":"reasoning.text","text":"a"}]}}]}\n',
        'data: {"choices":[{"delta":{"reasoning":" more","reasoning_details":[{"id":"r1","type":"reasoning.text","text":"b"}]}}]}\n',
        "data: [DONE]\n",
    ]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.stream.return_value = _stream_response(chunks)
        mock_client_cls.return_value = mock_client

        result = client.stream_completion([{"role": "user", "content": "hi"}])
        assert result.reasoning == "think more"
        assert result.reasoning_details == [
            {"id": "r1", "type": "reasoning.text", "text": "ab"}
        ]


def test_response_format_in_payload() -> None:
    cfg = _config()
    client = OpenRouterClient(cfg)
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    response_format = build_response_format(schema)
    ok_chunks = [
        'data: {"choices":[{"delta":{"content":"{}"}}]}\n',
        "data: [DONE]\n",
    ]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.stream.return_value = _stream_response(ok_chunks)
        mock_client_cls.return_value = mock_client

        client.stream_completion(
            [{"role": "user", "content": "hi"}],
            response_format=response_format,
        )
        payload = mock_client.stream.call_args.kwargs["json"]
        assert payload["response_format"] == response_format


def test_optional_payload_fields() -> None:
    cfg = _config(
        reasoning_effort="medium",
        openrouter=OpenRouterSettings(
            max_tokens=4096,
            user_id="user-123",
            require_parameters=True,
            reasoning_exclude=True,
        ),
    )
    client = OpenRouterClient(cfg)
    payload = client._build_payload(
        "test/model",
        [{"role": "user", "content": "hi"}],
        fallback_models=None,
        tools=[{"type": "function", "function": {"name": "shell"}}],
        response_format=None,
    )
    assert payload["max_tokens"] == 4096
    assert payload["user"] == "user-123"
    assert payload["provider"] == {"require_parameters": True}
    assert payload["reasoning"] == {"effort": "medium", "exclude": True}


def test_choice_error_raises() -> None:
    cfg = _config(openrouter=OpenRouterSettings(max_retries=0, fallback_models=[]))
    client = OpenRouterClient(cfg)
    chunks = [
        'data: {"choices":[{"error":{"message":"bad choice"}}]}\n',
        "data: [DONE]\n",
    ]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.stream.return_value = _stream_response(chunks)
        mock_client_cls.return_value = mock_client

        with pytest.raises(Exception, match="choice error"):
            client.stream_completion([{"role": "user", "content": "hi"}])
