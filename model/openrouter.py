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

RETRYABLE_STATUS = {429, 502, 503, 504}


@dataclass
class CompletionResult:
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    usage: dict[str, int] | None


class OpenRouterError(Exception):
    pass


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
    ) -> str:
        cancel_token = cancel_token or CancelToken()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
        }

        def do_post(client: httpx.Client) -> httpx.Response:
            return client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )

        response = self._request_with_retry(do_post, cancel_token=cancel_token)
        data = response.json()
        return data["choices"][0]["message"]["content"] or ""

    def stream_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
        *,
        cancel_token: CancelToken | None = None,
    ) -> CompletionResult:
        cancel_token = cancel_token or CancelToken()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] | None = None

        attempt = 0
        while True:
            cancel_token.check()
            try:
                timeout = httpx.Timeout(
                    float(self._settings.request_timeout_sec), connect=30.0
                )
                with httpx.Client(timeout=timeout) as client:
                    with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    ) as response:
                        if response.status_code != 200:
                            body = response.read().decode("utf-8", errors="replace")
                            if (
                                response.status_code in RETRYABLE_STATUS
                                and attempt < self._settings.max_retries
                            ):
                                self._sleep_backoff(
                                    attempt, response.headers.get("Retry-After")
                                )
                                attempt += 1
                                continue
                            self._raise_api_error(response.status_code, body)

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

                            if chunk.get("usage"):
                                usage = chunk["usage"]

                            choices = chunk.get("choices", [])
                            if not choices:
                                continue

                            delta = choices[0].get("delta", {})
                            finish_reason = (
                                choices[0].get("finish_reason") or finish_reason
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
            except httpx.HTTPError as exc:
                if attempt < self._settings.max_retries:
                    self._sleep_backoff(attempt)
                    attempt += 1
                    continue
                raise OpenRouterError(
                    f"HTTP error contacting OpenRouter after retries: {exc}"
                ) from exc

        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        return CompletionResult(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
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
                        response.status_code, response.text
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
            "HTTP-Referer": "https://github.com/agent-cli",
            "X-Title": "agent-cli",
        }

    def _raise_api_error(self, status: int, body: str) -> None:
        hint = ""
        if status in (401, 403):
            hint = " Check your OPENROUTER_API_KEY."
        elif status == 404:
            hint = f" Model '{self.config.model}' may not exist on OpenRouter."
        elif status == 429:
            hint = " Rate limited by OpenRouter. Wait and retry or reduce request frequency."
        elif status in (502, 503, 504):
            hint = " OpenRouter upstream temporarily unavailable."
        elif status == 400 and "tool" in body.lower():
            hint = (
                f" Tool calling may not be supported by model '{self.config.model}'."
                " Try another model with --model, e.g. anthropic/claude-sonnet-4."
            )
        raise OpenRouterError(f"OpenRouter API error ({status}): {body}{hint}")
