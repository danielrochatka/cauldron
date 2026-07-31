"""Site public-URL capability contract.

A Cauldron site module (e.g. cauldron-site-astro) may register a
SitePublicUrlProvider so admin modules can surface live "View" links
without knowing the site's routing conventions.

If no provider is registered, get_public_url() returns None; admin UIs
should hide or disable public-URL actions gracefully so deployments
without a public site capability continue to work.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SitePublicUrlProvider(Protocol):
    def get_public_url(self, *, item_id: str, slug: str, collection: str) -> str | None:
        ...


_provider: SitePublicUrlProvider | None = None


def register_public_url_provider(provider: SitePublicUrlProvider | None) -> None:
    global _provider
    _provider = provider


def get_public_url(*, item_id: str, slug: str, collection: str) -> str | None:
    if _provider is None:
        return None
    return _provider.get_public_url(item_id=item_id, slug=slug, collection=collection)


def has_public_url_provider() -> bool:
    return _provider is not None


__all__ = [
    "SitePublicUrlProvider",
    "register_public_url_provider",
    "get_public_url",
    "has_public_url_provider",
]
