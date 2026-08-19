"""Tests for SafeUrlFetcher SSRF protection and basic fetch behaviour."""
from __future__ import annotations

import io
import socket
import unittest.mock as mock

import pytest

from cauldron_ai_web.fetcher import SafeUrlFetcher, UnsafeUrlError, UrlFetchError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(body: bytes, content_type: str = "text/html", status: int = 200):
    """Build a mock urllib response context manager."""
    resp = mock.MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.geturl.return_value = "https://example.com/"

    chunks = [body[i:i+8192] for i in range(0, len(body), 8192)]
    chunks.append(b"")
    resp.read.side_effect = chunks

    return resp


def _patch_opener(response_mock):
    return mock.patch(
        "cauldron_ai_web.fetcher.urllib.request.build_opener",
        return_value=mock.MagicMock(
            open=mock.MagicMock(return_value=response_mock)
        ),
    )


def _patch_dns(ip: str = "93.184.216.34"):
    """Patch getaddrinfo to return a single public IP."""
    return mock.patch(
        "cauldron_ai_web.fetcher.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))],
    )


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

class TestUrlValidation:
    def test_rejects_empty_url(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError, match="non-empty"):
            fetcher._validate_url("")

    def test_rejects_ftp_scheme(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError, match="not allowed"):
            fetcher._validate_url("ftp://example.com/file.txt")

    def test_rejects_file_scheme(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError, match="not allowed"):
            fetcher._validate_url("file:///etc/passwd")

    def test_rejects_too_long_url(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError, match="too long"):
            fetcher._validate_url("https://example.com/" + "a" * 2048)

    def test_accepts_http_url(self):
        fetcher = SafeUrlFetcher()
        fetcher._validate_url("http://example.com/page")  # no exception

    def test_accepts_https_url(self):
        fetcher = SafeUrlFetcher()
        fetcher._validate_url("https://example.com/page")  # no exception


# ---------------------------------------------------------------------------
# SSRF host validation
# ---------------------------------------------------------------------------

class TestSsrfProtection:
    def test_blocks_localhost(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError, match="not permitted"):
            fetcher._validate_host("localhost")

    def test_blocks_localhost_localdomain(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError, match="not permitted"):
            fetcher._validate_host("localhost.localdomain")

    def test_blocks_private_10_x(self):
        fetcher = SafeUrlFetcher()
        with mock.patch(
            "cauldron_ai_web.fetcher.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))],
        ):
            with pytest.raises(UnsafeUrlError, match="private"):
                fetcher._validate_host("internal.corp")

    def test_blocks_private_192_168(self):
        fetcher = SafeUrlFetcher()
        with mock.patch(
            "cauldron_ai_web.fetcher.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.100", 0))],
        ):
            with pytest.raises(UnsafeUrlError, match="private"):
                fetcher._validate_host("myserver.local")

    def test_blocks_link_local(self):
        fetcher = SafeUrlFetcher()
        with mock.patch(
            "cauldron_ai_web.fetcher.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.1.1", 0))],
        ):
            with pytest.raises(UnsafeUrlError, match="private"):
                fetcher._validate_host("link.local")

    def test_allows_public_ip(self):
        fetcher = SafeUrlFetcher()
        with _patch_dns("93.184.216.34"):
            fetcher._validate_host("example.com")  # no exception

    def test_raises_fetch_error_on_dns_failure(self):
        fetcher = SafeUrlFetcher()
        with mock.patch(
            "cauldron_ai_web.fetcher.socket.getaddrinfo",
            side_effect=socket.gaierror("Name not found"),
        ):
            with pytest.raises(UrlFetchError, match="Could not resolve"):
                fetcher._validate_host("nonexistent.invalid")


# ---------------------------------------------------------------------------
# Content type validation
# ---------------------------------------------------------------------------

class TestContentTypeValidation:
    def test_allows_text_html(self):
        SafeUrlFetcher()._validate_content_type("text/html", "https://x.com")

    def test_allows_text_css(self):
        SafeUrlFetcher()._validate_content_type("text/css", "https://x.com")

    def test_rejects_application_json(self):
        with pytest.raises(UrlFetchError, match="Unexpected content type"):
            SafeUrlFetcher()._validate_content_type("application/json", "https://x.com")

    def test_rejects_image_png(self):
        with pytest.raises(UrlFetchError, match="Unexpected content type"):
            SafeUrlFetcher()._validate_content_type("image/png", "https://x.com")

    def test_allows_empty_content_type(self):
        SafeUrlFetcher()._validate_content_type("", "https://x.com")  # no exception


# ---------------------------------------------------------------------------
# Full fetch
# ---------------------------------------------------------------------------

class TestFetch:
    def test_successful_fetch(self):
        fetcher = SafeUrlFetcher()
        body = b"<html><body>Hello</body></html>"
        resp_mock = _make_mock_response(body, "text/html")

        with _patch_dns(), _patch_opener(resp_mock):
            result = fetcher.fetch("https://example.com/")

        assert result.body_bytes == body
        assert result.status_code == 200

    def test_truncates_large_response(self):
        fetcher = SafeUrlFetcher(max_bytes=100)
        large_body = b"x" * 10_000
        resp_mock = _make_mock_response(large_body, "text/html")

        with _patch_dns(), _patch_opener(resp_mock):
            result = fetcher.fetch("https://example.com/")

        assert len(result.body_bytes) == 100

    def test_fetch_raises_on_url_error(self):
        import urllib.error
        fetcher = SafeUrlFetcher()

        opener_mock = mock.MagicMock()
        opener_mock.open.side_effect = urllib.error.URLError("connection refused")

        with _patch_dns():
            with mock.patch(
                "cauldron_ai_web.fetcher.urllib.request.build_opener",
                return_value=opener_mock,
            ):
                with pytest.raises(UrlFetchError, match="Failed to fetch"):
                    fetcher.fetch("https://example.com/")

    def test_fetch_blocks_ssrf_url(self):
        fetcher = SafeUrlFetcher()
        with pytest.raises(UnsafeUrlError):
            fetcher.fetch("http://localhost/admin")
