"""SSRF-protected HTTP/HTTPS URL fetcher."""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_REDIRECTS = 5
_TIMEOUT_SECONDS = 10.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
]


class _SSRFAwareRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that SSRF-validates every Location target before following it."""

    def __init__(self, validate_url, validate_host, max_redirects: int) -> None:
        self._validate_url = validate_url
        self._validate_host = validate_host
        self._max_redirects = max_redirects
        self._redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            raise UrlFetchError(
                f"Exceeded maximum of {self._max_redirects} redirects."
            )
        self._validate_url(newurl)
        parsed = urlparse(newurl)
        self._validate_host(parsed.hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body_bytes: bytes


class UrlFetchError(Exception):
    pass


class UnsafeUrlError(UrlFetchError):
    pass


class SafeUrlFetcher:
    def __init__(
        self,
        *,
        max_bytes: int = _MAX_RESPONSE_BYTES,
        max_redirects: int = _MAX_REDIRECTS,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        self._validate_url(url)
        parsed = urlparse(url)
        self._validate_host(parsed.hostname or "")

        redirect_handler = _SSRFAwareRedirectHandler(
            validate_url=self._validate_url,
            validate_host=self._validate_host,
            max_redirects=self._max_redirects,
        )
        opener = urllib.request.build_opener(redirect_handler)

        headers = {
            "User-Agent": "CauldronAI/1.0 (+https://cauldron.invalid/bot)",
            "Accept": "text/html,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        }
        request = urllib.request.Request(url, headers=headers)

        try:
            with opener.open(request, timeout=self._timeout) as resp:
                final_url = resp.geturl() or url
                status_code = resp.status if hasattr(resp, "status") else 200
                content_type = resp.headers.get("Content-Type", "")
                content_type_base = content_type.split(";")[0].strip().lower()

                self._validate_content_type(content_type_base, url)

                body = b""
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    body += chunk
                    if len(body) > self._max_bytes:
                        body = body[:self._max_bytes]
                        logger.warning(
                            "Response from %r truncated at %d bytes", url, self._max_bytes
                        )
                        break
        except UnsafeUrlError:
            raise
        except urllib.error.URLError as exc:
            raise UrlFetchError(f"Failed to fetch {url!r}: {exc.reason}") from exc
        except OSError as exc:
            raise UrlFetchError(f"Network error fetching {url!r}: {exc}") from exc
        except Exception as exc:
            raise UrlFetchError(f"Unexpected error fetching {url!r}: {exc}") from exc

        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            body_bytes=body,
        )

    def _validate_url(self, url: str) -> None:
        if not isinstance(url, str) or not url:
            raise UnsafeUrlError("URL must be a non-empty string.")
        if len(url) > 2048:
            raise UnsafeUrlError("URL is too long (max 2048 characters).")
        try:
            parsed = urlparse(url)
        except Exception as exc:
            raise UnsafeUrlError(f"Invalid URL: {exc}") from exc
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise UnsafeUrlError(
                f"URL scheme {parsed.scheme!r} is not allowed. "
                "Only http and https are permitted."
            )
        if not parsed.hostname:
            raise UnsafeUrlError("URL has no hostname.")

    def _validate_host(self, hostname: str) -> None:
        hostname = hostname.lower()
        if hostname in ("localhost", "localhost.localdomain"):
            raise UnsafeUrlError(f"Access to {hostname!r} is not permitted.")

        try:
            results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UrlFetchError(f"Could not resolve hostname {hostname!r}: {exc}") from exc

        for result in results:
            addr_str = result[4][0]
            try:
                addr = ipaddress.ip_address(addr_str)
            except ValueError:
                continue
            for network in _PRIVATE_NETWORKS:
                if addr in network:
                    raise UnsafeUrlError(
                        f"Access to private/internal address {addr_str!r} is not permitted."
                    )

    def _validate_content_type(self, content_type: str, url: str) -> None:
        allowed = {"text/html", "text/plain", "text/css", "application/xhtml+xml"}
        if content_type and content_type not in allowed:
            raise UrlFetchError(
                f"Unexpected content type {content_type!r} from {url!r}. "
                f"Expected HTML or CSS."
            )


def get_fetcher() -> SafeUrlFetcher:
    return SafeUrlFetcher()
