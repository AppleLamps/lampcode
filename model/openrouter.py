from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from agent.cancel import CancelToken
from agent.config import Config
from agent.providers.openrouter import (
    classify_http_status,
    model_chain,
    should_fallback,
    use_native_model_routing,
)
from agent.telemetry import trace_span

RETRYABLE_STATUS = {429, 502, 503, 504}


@dataclass
class CompletionResult:
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    usage: dict[str, Any] | None
    model_used: str = ""
    fallback_used: bool = False
    reasoning: str = ""
    reasoning_details: list[dict[str, Any]] = field(default_factory=list)


class OpenRouterError(Exception):
    pass


def _merge_reasoning_details(
    acc: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> None:
    for item in incoming:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not item_id:
            acc.append(dict(item))
            continue
        for index, existing in enumerate(acc):
            if existing.get("id") != item_id:
                continue
            merged = dict(existing)
            merged.update(item)
            if "text" in existing and "text" in item:
                merged["text"] = f"{existing.get('text', '')}{item.get('text', '')}"
            if "summary" in existing and "summary" in item:
                merged["summary"] = f"{existing.get('summary', '')}{item.get('summary', '')}"
            acc[index] = merged
            break
        else:
            acc.append(dict(item))


class OpenRouterClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.api_key = config.require_api_key()
        self.base_url = config.openrouter_base_url
        self._settings = config.openrouter

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        cancel_token: CancelToken | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        result = self.stream_completion(
            messages,
            cancel_token=cancel_token,
            response_format=response_format,
        )
        return result.content

    def stream_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
        *,
        cancel_token: CancelToken | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> CompletionResult:
        cancel_token = cancel_token or CancelToken()
        chain = model_chain(self.config)

        if use_native_model_routing(self.config, chain):
            return self._stream_request(
                messages,
                primary_model=chain.models[0],
                fallback_models=chain.models[1:],
                tools=tools,
                on_delta=on_delta,
                cancel_token=cancel_token,
                primary_for_fallback=chain.models[0],
                model_index=0,
                total_models=len(chain.models),
                response_format=response_format,
            )

        last_error: OpenRouterError | None = None
        for model_index, model_name in enumerate(chain.models):
            cancel_token.check()
            try:
                return self._stream_request(
                    messages,
                    primary_model=model_name,
                    fallback_models=None,
                    tools=tools,
                    on_delta=on_delta,
                    cancel_token=cancel_token,
                    primary_for_fallback=chain.models[0],
                    model_index=model_index,
                    total_models=len(chain.models),
                    response_format=response_format,
                )
            except OpenRouterError as exc:
                last_error = exc
                error_kind = getattr(exc, "error_kind", "client_error")
                if should_fallback(
                    settings=self._settings,
                    error_kind=error_kind,
                    model_index=model_index,
                    total_models=len(chain.models),
                ):
                    continue
                raise

        if last_error:
            raise last_error
        raise OpenRouterError("No models available in fallback chain")

    def _build_payload(
        self,
        primary_model: str,
        messages: list[dict[str, Any]],
        *,
        fallback_models: list[str] | None,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": primary_model,
            "messages": messages,
            "stream": True,
        }
        if fallback_models:
            payload["models"] = fallback_models
            payload["route"] = "fallback"
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        if self.config.reasoning_effort:
            reasoning: dict[str, Any] = {"effort": self.config.reasoning_effort}
            if self._settings.reasoning_exclude:
                reasoning["exclude"] = True
            payload["reasoning"] = reasoning
        if self._settings.max_tokens is not None:
            payload["max_tokens"] = self._settings.max_tokens
        if self._settings.user_id:
            payload["user"] = self._settings.user_id
        if self._settings.require_parameters and tools:
            payload["provider"] = {"require_parameters": True}
        return payload

    def _stream_request(
        self,
        messages: list[dict[str, Any]],
        primary_model: str,
        *,
        fallback_models: list[str] | None,
        tools: list[dict[str, Any]] | None,
        on_delta: Callable[[str], None] | None,
        cancel_token: CancelToken,
        primary_for_fallback: str,
        model_index: int,
        total_models: int,
        response_format: dict[str, Any] | None,
    ) -> CompletionResult:
        payload = self._build_payload(
            primary_model,
            messages,
            fallback_models=fallback_models,
            tools=tools,
            response_format=response_format,
        )
        without_schema = response_format is not None

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_details_acc: list[dict[str, Any]] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        response_model = primary_model

        with trace_span("model.completion", model=primary_model):
            attempt = 0
            while True:
                cancel_token.check()
                active_payload = payload
                if without_schema and attempt > 0:
                    active_payload = self._build_payload(
                        primary_model,
                        messages,
                        fallback_models=fallback_models,
                        tools=tools,
                        response_format=None,
                    )
                try:
                    timeout = httpx.Timeout(
                        float(self._settings.request_timeout_sec), connect=30.0
                    )
                    with httpx.Client(timeout=timeout) as client:
                        with client.stream(
                            "POST",
                            f"{self.base_url}/chat/completions",
                            headers=self._headers(),
                            json=active_payload,
                        ) as response:
                            if response.status_code != 200:
                                body = response.read().decode("utf-8", errors="replace")
                                error_kind = classify_http_status(response.status_code, body)
                                if (
                                    response.status_code in RETRYABLE_STATUS
                                    and attempt < self._settings.max_retries
                                ):
                                    self._sleep_backoff(
                                        attempt, response.headers.get("Retry-After")
                                    )
                                    attempt += 1
                                    continue
                                err = self._make_api_error(
                                    response.status_code, body, primary_model
                                )
                                err.error_kind = error_kind  # type: ignore[attr-defined]
                                if (
                                    without_schema
                                    and response.status_code == 400
                                    and attempt == 0
                                ):
                                    attempt += 1
                                    continue
                                if should_fallback(
                                    settings=self._settings,
                                    error_kind=error_kind,
                                    model_index=model_index,
                                    total_models=total_models,
                                ):
                                    raise err
                                raise err

                            for line in response.iter_lines():
                                cancel_token.check()
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break

                                chunk = json.loads(data_str)
                                if chunk.get("error"):
                                    raise OpenRouterError(
                                        f"OpenRouter error: {chunk['error']}"
                                    )

                                if chunk.get("model"):
                                    response_model = str(chunk["model"])

                                if chunk.get("usage"):
                                    usage = chunk["usage"]

                                choices = chunk.get("choices", [])
                                if not choices:
                                    continue

                                choice_err = choices[0].get("error")
                                if choice_err:
                                    raise OpenRouterError(
                                        f"OpenRouter choice error: {choice_err}"
                                    )

                                delta = choices[0].get("delta", {})
                                finish_reason = (
                                    choices[0].get("finish_reason") or finish_reason
                                )

                                if delta.get("reasoning"):
                                    reasoning_parts.append(str(delta["reasoning"]))
                                if delta.get("reasoning_details"):
                                    _merge_reasoning_details(
                                        reasoning_details_acc,
                                        list(delta["reasoning_details"]),
                                    )

                                if delta.get("content"):
                                    content_parts.append(delta["content"])
                                    if on_delta:
                                        on_delta(delta["content"])

                                for tc_delta in delta.get("tool_calls") or []:
                                    idx = tc_delta.get("index", 0)
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = {
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    acc = tool_calls_acc[idx]
                                    if tc_delta.get("id"):
                                        acc["id"] = tc_delta["id"]
                                    fn = tc_delta.get("function") or {}
                                    if fn.get("name"):
                                        acc["function"]["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        acc["function"]["arguments"] += fn["arguments"]
                    break
                except httpx.TimeoutException as exc:
                    err = OpenRouterError(
                        f"OpenRouter request timed out for model '{primary_model}'"
                    )
                    err.error_kind = "timeout"  # type: ignore[attr-defined]
                    if should_fallback(
                        settings=self._settings,
                        error_kind="timeout",
                        model_index=model_index,
                        total_models=total_models,
                    ):
                        raise err from exc
                    if attempt < self._settings.max_retries:
                        self._sleep_backoff(attempt)
                        attempt += 1
                        continue
                    raise err from exc
                except httpx.HTTPError as exc:
                    err = OpenRouterError(
                        f"HTTP error contacting OpenRouter after retries: {exc}"
                    )
                    err.error_kind = "provider_error"  # type: ignore[attr-defined]
                    if should_fallback(
                        settings=self._settings,
                        error_kind="provider_error",
                        model_index=model_index,
                        total_models=total_models,
                    ):
                        raise err from exc
                    if attempt < self._settings.max_retries:
                        self._sleep_backoff(attempt)
                        attempt += 1
                        continue
                    raise err from exc

        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        if fallback_models:
            fallback_used = response_model != primary_for_fallback
        else:
            fallback_used = model_index > 0
        return CompletionResult(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model_used=response_model,
            fallback_used=fallback_used,
            reasoning="".join(reasoning_parts),
            reasoning_details=reasoning_details_acc,
        )

    def _request_with_retry(
        self,
        fn: Callable[[httpx.Client], httpx.Response],
        *,
        cancel_token: CancelToken,
    ) -> httpx.Response:
        attempt = 0
        while True:
            cancel_token.check()
            try:
                timeout = httpx.Timeout(
                    float(self._settings.request_timeout_sec), connect=30.0
                )
                with httpx.Client(timeout=timeout) as client:
                    response = fn(client)
                if response.status_code in RETRYABLE_STATUS and attempt < self._settings.max_retries:
                    self._sleep_backoff(attempt, response.headers.get("Retry-After"))
                    attempt += 1
                    continue
                if response.status_code != 200:
                    self._raise_api_error(
                        response.status_code, response.text, self.config.model
                    )
                return response
            except httpx.HTTPError as exc:
                if attempt < self._settings.max_retries:
                    self._sleep_backoff(attempt)
                    attempt += 1
                    continue
                raise OpenRouterError(
                    f"HTTP error contacting OpenRouter after retries: {exc}"
                ) from exc

    def _sleep_backoff(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                delay = max(
                    0.0,
                    parsedate_to_datetime(retry_after).timestamp() - time.time(),
                )
            except (TypeError, ValueError, OSError):
                delay = self._settings.retry_base_delay_sec * (2**attempt)
        else:
            delay = self._settings.retry_base_delay_sec * (2**attempt)
        jitter = random.uniform(0, delay * 0.25)
        time.sleep(delay + jitter)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._settings.app_url or "https://github.com/agent-cli",
            "X-Title": self._settings.app_name or "agent-cli",
        }

    def _make_api_error(self, status: int, body: str, model_name: str) -> OpenRouterError:
        hint = ""
        if status in (401, 403):
            hint = " Check your OPENROUTER_API_KEY."
        elif status == 404:
            hint = (
                f" Model '{model_name}' may not exist on OpenRouter."
                " Run `agent models list` to see available models."
            )
        elif status == 429:
            hint = " Rate limited by OpenRouter. Wait and retry or reduce request frequency."
        elif status in (502, 503, 504):
            hint = " OpenRouter upstream temporarily unavailable."
        elif status == 400 and "tool" in body.lower():
            hint = (
                f" Tool calling may not be supported by model '{model_name}'."
                " Try another model with --model, e.g. openrouter/owl-alpha."
            )
        elif status == 400 and classify_http_status(status, body) == "context_length":
            hint = " Context length exceeded. Try compaction or a shorter thread."
        return OpenRouterError(f"OpenRouter API error ({status}): {body}{hint}")

    def _raise_api_error(self, status: int, body: str, model_name: str) -> None:
        raise self._make_api_error(status, body, model_name)
