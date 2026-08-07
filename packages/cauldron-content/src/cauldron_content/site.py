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
_provider_owning_module: str = ""


def register_public_url_provider(
    provider: SitePublicUrlProvider | None,
    owning_module: str = "",
) -> None:
    """Register the site public-URL provider.

    Passing ``None`` clears the provider and its recorded owner.

    Re-registering the same provider class (including autoreload re-execution
    that creates a fresh instance of the same type) is idempotent provided the
    owning module is consistent.  Registering a provider of a *different* class,
    or registering the same class with a conflicting non-empty owner, raises
    ``ValueError`` — only one site module may own this capability.
    """
    global _provider, _provider_owning_module
    if provider is None:
        _provider = None
        _provider_owning_module = ""
        return
    if _provider is not None:
        if type(_provider) is not type(provider):
            owner_hint = (
                f" (registered by {_provider_owning_module!r})"
                if _provider_owning_module else ""
            )
            raise ValueError(
                f"A SitePublicUrlProvider of type "
                f"{type(_provider).__qualname__!r} is already registered"
                f"{owner_hint}. Only one site module may provide this capability."
            )
        # Same type — check for ownership conflict before treating as idempotent.
        if (
            owning_module
            and _provider_owning_module
            and owning_module != _provider_owning_module
        ):
            raise ValueError(
                f"A SitePublicUrlProvider owned by {_provider_owning_module!r} is already "
                f"registered. Cannot re-register with owning_module={owning_module!r}."
            )
        # Idempotent re-registration (e.g. Django autoreload).
        _provider = provider
        if owning_module:
            _provider_owning_module = owning_module
        return
    _provider = provider
    _provider_owning_module = owning_module


def _reset_public_url_provider_for_tests() -> None:
    """Clear the registered provider. For test isolation only."""
    global _provider, _provider_owning_module
    _provider = None
    _provider_owning_module = ""


def get_public_url(*, item_id: str, slug: str, collection: str) -> str | None:
    if _provider is None:
        return None
    return _provider.get_public_url(item_id=item_id, slug=slug, collection=collection)


def has_public_url_provider() -> bool:
    return _provider is not None


def get_public_url_provider_owning_module() -> str:
    """Return the owning module slug of the registered provider, or ''."""
    return _provider_owning_module


__all__ = [
    "SitePublicUrlProvider",
    "register_public_url_provider",
    "get_public_url",
    "has_public_url_provider",
    "get_public_url_provider_owning_module",
    "_reset_public_url_provider_for_tests",
]
