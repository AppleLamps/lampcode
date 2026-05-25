from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import httpx

from agent.models import WebSearchResult
from agent.settings import WebSearchSettings


def web_search(query: str, settings: WebSearchSettings) -> tuple[list[WebSearchResult], str | None]:
    if settings.provider == "duckduckgo":
        return _duckduckgo_html(query, settings)
    return [], f"Unknown web search provider: {settings.provider}"


def _duckduckgo_html(query: str, settings: WebSearchSettings) -> tuple[list[WebSearchResult], str | None]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        with httpx.Client(timeout=float(settings.timeout_sec), follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": "agent-cli/0.6.0"},
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
