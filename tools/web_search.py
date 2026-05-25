from __future__ import annotations

import json
import os
import re
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import httpx

from agent.models import WebSearchResult
from agent.settings import WebSearchSettings


def web_search(query: str, settings: WebSearchSettings) -> tuple[list[WebSearchResult], str | None]:
    provider = settings.provider.lower()
    if provider == "duckduckgo":
        return _duckduckgo_html(query, settings)
    if provider == "exa":
        return _search_exa(query, settings)
    if provider == "tavily":
        return _search_tavily(query, settings)
    return [], f"Unknown web search provider: {settings.provider}"


def _resolve_api_key(settings: WebSearchSettings) -> str | None:
    if settings.api_key:
        return settings.api_key
    if settings.api_key_env:
        return os.environ.get(settings.api_key_env)
    return None


def _search_exa(query: str, settings: WebSearchSettings) -> tuple[list[WebSearchResult], str | None]:
    api_key = _resolve_api_key(settings)
    if not api_key:
        env_name = settings.api_key_env or "EXA_API_KEY"
        return [], f"Missing API key: set {env_name} or web_search.api_key in config"

    payload = {
        "query": query,
        "numResults": settings.max_results,
        "type": "auto",
    }
    try:
        with httpx.Client(timeout=float(settings.timeout_sec)) as client:
            response = client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        return [], str(exc)
    except json.JSONDecodeError as exc:
        return [], str(exc)

    return _map_exa_results(data, settings.max_results), None


def _map_exa_results(data: dict[str, Any], max_results: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    for item in data.get("results", [])[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("url") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("text") or item.get("snippet") or "").strip()
        if title and url:
            results.append(WebSearchResult(title=title, url=url, snippet=snippet))
    return results


def _search_tavily(query: str, settings: WebSearchSettings) -> tuple[list[WebSearchResult], str | None]:
    api_key = _resolve_api_key(settings)
    if not api_key:
        env_name = settings.api_key_env or "TAVILY_API_KEY"
        return [], f"Missing API key: set {env_name} or web_search.api_key in config"

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": settings.max_results,
    }
    try:
        with httpx.Client(timeout=float(settings.timeout_sec)) as client:
            response = client.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        return [], str(exc)
    except json.JSONDecodeError as exc:
        return [], str(exc)

    return _map_tavily_results(data, settings.max_results), None


def _map_tavily_results(data: dict[str, Any], max_results: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    for item in data.get("results", [])[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("url") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        if title and url:
            results.append(WebSearchResult(title=title, url=url, snippet=snippet))
    return results


def _duckduckgo_html(query: str, settings: WebSearchSettings) -> tuple[list[WebSearchResult], str | None]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        with httpx.Client(timeout=float(settings.timeout_sec), follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": "agent-cli/0.9.0"},
            )
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        return [], str(exc)

    results: list[WebSearchResult] = []
    for block in re.finditer(
        r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</(?:a|td|div)>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        title = _strip_tags(block.group("title"))
        snippet = _strip_tags(block.group("snippet"))
        href = unescape(block.group("url"))
        if title and href:
            results.append(WebSearchResult(title=title, url=href, snippet=snippet))
        if len(results) >= settings.max_results:
            break

    if not results:
        instant = _duckduckgo_instant(query, settings)
        if instant:
            results.append(instant)

    return results, None


def _duckduckgo_instant(query: str, settings: WebSearchSettings) -> WebSearchResult | None:
    url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_redirect=1"
    try:
        with httpx.Client(timeout=float(settings.timeout_sec)) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return None

    abstract = (data.get("AbstractText") or "").strip()
    abstract_url = (data.get("AbstractURL") or "").strip()
    heading = (data.get("Heading") or query).strip()
    if abstract and abstract_url:
        return WebSearchResult(title=heading, url=abstract_url, snippet=abstract)
    return None


def _strip_tags(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", text)
    return unescape(cleaned).strip()


def format_results_for_model(results: list[WebSearchResult]) -> str:
    if not results:
        return "No web search results found."
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r.title}\n   URL: {r.url}\n   {r.snippet}")
    return "\n\n".join(lines)
