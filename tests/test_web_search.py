from unittest.mock import MagicMock, patch

from agent.models import WebSearchResult
from agent.settings import WebSearchSettings
from tools.web_search import format_results_for_model, web_search


def test_format_results_for_model() -> None:
    results = [
        WebSearchResult(title="A", url="https://a", snippet="snippet a"),
    ]
    text = format_results_for_model(results)
    assert "A" in text
    assert "https://a" in text


def test_web_search_mocked_html() -> None:
    html = """
    <a rel="nofollow" class="result__a" href="https://example.com">Example</a>
    <td class="result__snippet">A snippet here</td>
    """
    settings = WebSearchSettings(max_results=3, timeout_sec=5)
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()

    with patch("tools.web_search.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = mock_response
        client_cls.return_value = client

        results, error = web_search("test query", settings)
    assert error is None
    assert len(results) >= 1
    assert results[0].url == "https://example.com"


def test_web_search_http_error() -> None:
    settings = WebSearchSettings(timeout_sec=5)
    import httpx

    with patch("tools.web_search.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = httpx.ConnectError("fail")
        client_cls.return_value = client

        results, error = web_search("x", settings)
    assert results == []
    assert error is not None
