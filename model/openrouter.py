from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from agent.config import Config


@dataclass
class StreamDelta:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


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

    def stream_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> CompletionResult:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/agent-cli",
            "X-Title": "agent-cli",
        }

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] | None = None

        try:
            with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", errors="replace")
                        self._raise_api_error(response.status_code, body)

                    for line in response.iter_lines():
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

                        if "usage" in chunk and chunk["usage"]:
                            usage = chunk["usage"]

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason") or finish_reason

                        if "content" in delta and delta["content"]:
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

        except httpx.HTTPError as exc:
            raise OpenRouterError(f"HTTP error contacting OpenRouter: {exc}") from exc

        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        return CompletionResult(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _raise_api_error(self, status: int, body: str) -> None:
        hint = ""
        if status in (401, 403):
            hint = " Check your OPENROUTER_API_KEY."
        elif status == 400 and "tool" in body.lower():
            hint = (
                f" Tool calling may not be supported by model '{self.config.model}'."
                " Try another model with --model, e.g. anthropic/claude-sonnet-4."
            )
        raise OpenRouterError(f"OpenRouter API error ({status}): {body}{hint}")
