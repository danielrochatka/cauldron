"""Registration seam for public-site pages-style composition.

This module holds a single optional provider slot.  Callers must not import
from the concrete provider package (cauldron-django-admin) — they use the
accessor here instead.

``cauldron-django-admin`` registers a ``PagesStyleProvider`` during its
``AppConfig.ready()``.  ``cauldron-site-astro`` reads the provider during
``SiteChangeSetService.prepare()`` so that content-only publishes always carry
the current composed pages CSS, and style proposals flow through the full
controlled-publication lifecycle.

If no provider is registered (e.g. the override store module is absent), all
accessors return safe no-op results so site-astro degrades gracefully.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PagesStyleProvider(Protocol):
    """Provides composed public-site pages CSS to the publication workflow."""

    def get_composed_css(
        self,
        *,
        proposed_target: str | None = None,
        proposed_content: str | None = None,
    ) -> str:
        """Return the effective public-site CSS.

        When *proposed_target* / *proposed_content* are given, overlay that
        one file in the composition (lexical sort order preserved).  Otherwise
        return only the currently-applied files concatenated.

        Returns empty string when there are no pages override files and no
        proposed content.
        """
        ...

    def commit_style(
        self,
        *,
        target: str,
        content: str,
        expected_hash: str,
        base_exists: bool,
    ) -> str:
        """Write a proposed CSS file to the store.

        Uses optimistic locking via *expected_hash* so concurrent modifications
        fail rather than silently overwriting.  Returns the new hash of the
        written content.

        Raises ``HashConflictError`` (from ``cauldron_django_admin.override_store``)
        if *expected_hash* does not match the current on-disk state.
        """
        ...

    def list_targets(self) -> list[str]:
        """Return sorted list of current pages CSS relative paths."""
        ...


_provider: PagesStyleProvider | None = None


def register_pages_style_provider(provider: PagesStyleProvider | None) -> None:
    """Register (or clear) the public-site pages-style provider.

    Only one provider may be registered at a time.  Calling with ``None``
    clears the registration.  Re-registering the same instance is idempotent.
    """
    global _provider
    _provider = provider


def get_pages_style_provider() -> PagesStyleProvider | None:
    """Return the registered provider, or ``None`` if none is registered."""
    return _provider
