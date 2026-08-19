"""Tests for web.inspect_url tool handler."""
from __future__ import annotations

import socket
import unittest.mock as mock

import pytest


def _make_context(user=None):
    ctx = mock.MagicMock()
    ctx.actor = user or mock.MagicMock()
    ctx.dry_run = False
    return ctx


def _make_fetch_result(body: bytes, content_type: str = "text/html", url: str = "https://example.com/"):
    from cauldron_ai_web.fetcher import FetchResult
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body_bytes=body,
    )


def _patch_dns(ip: str = "93.184.216.34"):
    return mock.patch(
        "cauldron_ai_web.fetcher.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))],
    )


SAMPLE_HTML = b"""<!DOCTYPE html>
<html>
<head><title>Test Page</title><link rel="stylesheet" href="/style.css"></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<h1>Welcome</h1>
<h2>Our Work</h2>
<div class="card"><p>A card element</p></div>
</body>
</html>"""

SAMPLE_CSS = b"""
body { font-family: Georgia, serif; background-color: #ffffff; color: #222; }
.card { border-radius: 6px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
"""


class TestWebInspectUrlTool:
    def _call(self, url: str, html: bytes = SAMPLE_HTML, css: bytes = SAMPLE_CSS):
        from cauldron_ai_web.tools import _handle_web_inspect_url

        html_result = _make_fetch_result(html)
        css_result = _make_fetch_result(css, "text/css", "https://example.com/style.css")

        ctx = _make_context()

        with _patch_dns():
            with mock.patch("cauldron_ai_web.tools.get_fetcher") as mock_get_fetcher:
                fetcher_mock = mock.MagicMock()
                mock_get_fetcher.return_value = fetcher_mock
                # First call returns HTML, second returns CSS
                fetcher_mock.fetch.side_effect = [html_result, css_result]
                return _handle_web_inspect_url(ctx, url=url)

    def test_successful_inspection(self):
        result = self._call("https://example.com/")
        assert result.success is True
        assert result.tool_name == "web.inspect_url"

    def test_returns_title(self):
        result = self._call("https://example.com/")
        assert result.data["title"] == "Test Page"

    def test_returns_headings(self):
        result = self._call("https://example.com/")
        assert "Welcome" in result.data["headings"]
        assert "Our Work" in result.data["headings"]

    def test_returns_nav_items(self):
        result = self._call("https://example.com/")
        assert "Home" in result.data["nav_items"]
        assert "About" in result.data["nav_items"]

    def test_returns_design_data_from_css(self):
        result = self._call("https://example.com/")
        assert len(result.data["font_families"]) > 0

    def test_detects_cards(self):
        result = self._call("https://example.com/")
        assert result.data["uses_cards"] is True

    def test_invalid_url_returns_error(self):
        from cauldron_ai_web.tools import _handle_web_inspect_url
        ctx = _make_context()
        result = _handle_web_inspect_url(ctx, url="")
        assert result.tool_name == "web.inspect_url"
        assert result.error_code == "tool.invalid_arguments"

    def test_ssrf_blocked_url_returns_error(self):
        from cauldron_ai_web.tools import _handle_web_inspect_url
        from cauldron_ai_web.fetcher import UnsafeUrlError
        ctx = _make_context()

        with mock.patch("cauldron_ai_web.tools.get_fetcher") as mock_get_fetcher:
            fetcher_mock = mock.MagicMock()
            mock_get_fetcher.return_value = fetcher_mock
            fetcher_mock.fetch.side_effect = UnsafeUrlError("private address blocked")
            result = _handle_web_inspect_url(ctx, url="http://192.168.1.1/")

        assert result.error_code == "web.unsafe_url"

    def test_network_error_returns_fetch_error(self):
        from cauldron_ai_web.tools import _handle_web_inspect_url
        from cauldron_ai_web.fetcher import UrlFetchError
        ctx = _make_context()

        with mock.patch("cauldron_ai_web.tools.get_fetcher") as mock_get_fetcher:
            fetcher_mock = mock.MagicMock()
            mock_get_fetcher.return_value = fetcher_mock
            fetcher_mock.fetch.side_effect = UrlFetchError("connection refused")
            result = _handle_web_inspect_url(ctx, url="https://unreachable.example.com/")

        assert result.error_code == "web.fetch_failed"

    def test_register_adds_tool_to_registry(self):
        from cauldron_ai_admin.tools import AdminAIToolRegistry
        from cauldron_ai_web.tools import register

        registry = AdminAIToolRegistry()
        register(registry)
        tool_names = [t.name for t in registry.all_definitions()]
        assert "web.inspect_url" in tool_names

    def test_css_fetch_failure_does_not_crash(self):
        """CSS fetch failure is gracefully handled; HTML analysis still returned."""
        from cauldron_ai_web.tools import _handle_web_inspect_url
        from cauldron_ai_web.fetcher import UrlFetchError

        html_result = _make_fetch_result(SAMPLE_HTML)
        ctx = _make_context()

        with _patch_dns():
            with mock.patch("cauldron_ai_web.tools.get_fetcher") as mock_get_fetcher:
                fetcher_mock = mock.MagicMock()
                mock_get_fetcher.return_value = fetcher_mock
                # HTML succeeds, CSS fails
                fetcher_mock.fetch.side_effect = [
                    html_result,
                    UrlFetchError("CSS not found"),
                ]
                result = _handle_web_inspect_url(ctx, url="https://example.com/")

        assert result.success is True
        assert result.data["title"] == "Test Page"
